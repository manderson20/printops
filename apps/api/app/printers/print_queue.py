"""What is actually waiting to print, asked of cupsd rather than the database.

A job only gets a `jobs` row once PrintOps' CUPS backend has started running
for it, so everything still queued behind the job at the head is invisible in
the database — during the LCACTC Kyocera outage three jobs sat queued and
exactly one had a row (see app/printers/job_control.py:queue_snapshot). A
queue view built on the `jobs` table would therefore show a person everything
except the thing they came to look at: the line they are standing in.

So this reads cupsd directly, in one Get-Jobs call against the server URI
rather than one per printer — 53 round trips per page load would be a page
load nobody waits for.

**Why the request carries requesting-user-name.** cupsd's default policy makes
`job-name` and `job-originating-user-name` private to the job's owner and to
SystemGroup, and it decides which you are from the requesting user on the
request itself. Omit it and every job comes back anonymous — including, quietly
and unhelpfully, the caller's own. The API process's OS user is in the
lpadmin group, which is the SystemGroup here, so naming it is what makes the
owner readable. Nothing about that widens what any *person* may see: the
router (app/routers/print_queue.py) is what decides that, and it shows a
non-admin nothing about anyone else's job but its size and place in line.
"""

import getpass
import logging
import plistlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from app.printers.job_control import (
    JOB_STATE_PENDING,
    JOB_STATE_PENDING_HELD,
    JOB_STATE_PROCESSING,
    JOB_STATE_PROCESSING_STOPPED,
)

logger = logging.getLogger(__name__)

IPPTOOL_TIMEOUT_SECONDS = 10

QUEUE_NAME_PREFIX = "printops-"

# CUPS prints in descending priority and breaks ties by submission time, so a
# job dropped to the floor waits behind every job at the default — including
# ones sent after it, which is exactly what "let others go first" means and
# what the UI has to say out loud.
NORMAL_PRIORITY = 50
YIELDED_PRIORITY = 1

# What each yielded job's priority was before it yielded, so "put back in
# line" returns it to where it actually was rather than assuming everything
# starts at the default. Restore is offered only for a job in here, which is
# what keeps the no-queue-jumping guarantee true in the corner where a client
# submitted its own low priority deliberately: PrintOps will not raise a job
# it did not lower.
#
# In-process and lost on restart, deliberately — the same admission
# app/printers/queue_recovery.py makes about its backoff state. Persisting it
# would mean a `jobs` row for something that has no row (that is the whole
# reason this module reads cupsd), and the failure mode is mild: a yielded job
# stops offering the undo and simply prints last, which is what its owner
# asked for.
_YIELDED_FROM: dict[tuple[str | None, int], int] = {}


def remember_priority_before_yield(printer_id: str | None, cups_job_id: int, priority: int) -> None:
    _YIELDED_FROM[(printer_id, cups_job_id)] = priority


def priority_before_yield(printer_id: str | None, cups_job_id: int) -> int | None:
    return _YIELDED_FROM.get((printer_id, cups_job_id))


def forget_priority_before_yield(printer_id: str | None, cups_job_id: int) -> None:
    _YIELDED_FROM.pop((printer_id, cups_job_id), None)


# The states a job can be in while it is still someone's to wait for. A
# stopped queue's jobs are included deliberately: they are the ones a person is
# most likely to be looking for an explanation of.
VISIBLE_STATES = (
    JOB_STATE_PENDING,
    JOB_STATE_PENDING_HELD,
    JOB_STATE_PROCESSING,
    JOB_STATE_PROCESSING_STOPPED,
)

_REQUESTED_ATTRIBUTES = (
    "job-id",
    "job-name",
    "job-originating-user-name",
    "job-state",
    "job-priority",
    "job-k-octets",
    "time-at-creation",
    "job-printer-uri",
    # Not shown to anyone — used to resolve whose job this is when CUPS
    # records a bare local username rather than an email (see
    # app/attribution/resolve.py, and the router's _ownership).
    "job-originating-host-name",
)


@dataclass(frozen=True)
class QueuedJob:
    cups_job_id: int
    # The PrintOps printer UUID, parsed out of the queue name. None for a
    # queue cupsd has that PrintOps didn't create.
    printer_id: str | None
    owner: str | None
    # The host CUPS recorded the job as coming from, which is what turns a
    # bare "matt" into a person via the device that sent it.
    source_host: str | None
    document_name: str | None
    size_bytes: int | None
    priority: int
    state: int
    created_at: datetime | None

    @property
    def is_waiting(self) -> bool:
        return self.state in (JOB_STATE_PENDING, JOB_STATE_PROCESSING_STOPPED)

    @property
    def is_printing(self) -> bool:
        return self.state == JOB_STATE_PROCESSING

    @property
    def is_held(self) -> bool:
        return self.state == JOB_STATE_PENDING_HELD

    @property
    def is_yielded(self) -> bool:
        return self.priority < NORMAL_PRIORITY

    def belongs_to(self, *identities: str | None) -> bool:
        """Whether this job was submitted by any of the given identities.

        Case-insensitively, because what CUPS records is whatever the client
        sent: the same person reaches this server as `hfiala@brookfieldr3.org`
        from a Mac and could reach it in another case from elsewhere. An
        ownership check that misses is not a cosmetic bug here — it is the
        difference between someone seeing their own document name and not."""
        if not self.owner:
            return False
        owner = self.owner.casefold()
        return any(i and i.casefold() == owner for i in identities)


def _printer_id_from_queue_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    queue_name = uri.rstrip("/").rsplit("/", 1)[-1]
    if not queue_name.startswith(QUEUE_NAME_PREFIX):
        return None
    return queue_name.removeprefix(QUEUE_NAME_PREFIX) or None


def _as_job(attributes: dict) -> QueuedJob | None:
    job_id = attributes.get("job-id")
    if not isinstance(job_id, int):
        return None
    created = attributes.get("time-at-creation")
    size_kb = attributes.get("job-k-octets")
    return QueuedJob(
        cups_job_id=job_id,
        printer_id=_printer_id_from_queue_uri(attributes.get("job-printer-uri")),
        owner=attributes.get("job-originating-user-name"),
        source_host=attributes.get("job-originating-host-name"),
        document_name=attributes.get("job-name"),
        # job-k-octets is kilobytes, and rounds a small job to 0 rather than
        # to nothing — keep it as a real size so the UI can say "12 KB".
        size_bytes=size_kb * 1024 if isinstance(size_kb, int) else None,
        priority=attributes.get("job-priority") or NORMAL_PRIORITY,
        state=attributes.get("job-state") or 0,
        created_at=datetime.fromtimestamp(created, UTC) if isinstance(created, int) else None,
    )


def queued_jobs() -> list[QueuedJob] | None:
    """Every job still queued anywhere on this server, in the order cupsd
    will print them.

    None means cupsd could not be asked, which is not the same as nobody
    having anything queued — a caller that conflates them tells a person with
    a job waiting that the queue is empty."""
    request = (
        "{\n"
        "    OPERATION Get-Jobs\n"
        "    GROUP operation-attributes-tag\n"
        "    ATTR charset attributes-charset utf-8\n"
        "    ATTR language attributes-natural-language en\n"
        "    ATTR uri printer-uri ipp://localhost/\n"
        f"    ATTR name requesting-user-name {getpass.getuser()}\n"
        "    ATTR keyword which-jobs not-completed\n"
        f"    ATTR keyword requested-attributes {','.join(_REQUESTED_ATTRIBUTES)}\n"
        "}\n"
    )
    try:
        result = subprocess.run(
            ["ipptool", "-X", "ipp://localhost/", "/dev/stdin"],
            input=request,
            capture_output=True,
            text=True,
            timeout=IPPTOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Could not ask cupsd what is queued.")
        return None

    try:
        parsed = plistlib.loads(result.stdout.encode())
        test = parsed["Tests"][0]
    except (plistlib.InvalidFileException, KeyError, IndexError, ValueError):
        return None
    if test.get("StatusCode") != "successful-ok":
        return None

    jobs = []
    for attributes in test.get("ResponseAttributes") or []:
        # The first group back is the operation's own attributes, not a job;
        # _as_job returns None for it because it has no job-id.
        job = _as_job(attributes)
        if job is not None and job.state in VISIBLE_STATES:
            jobs.append(job)
    return sorted(jobs, key=queue_order)


def queue_order(job: QueuedJob) -> tuple:
    """cupsd's own ordering: whatever is printing first, then everything
    eligible to print by descending priority and oldest first, then held jobs
    last of all.

    Held jobs sort to the bottom regardless of age or priority because they are
    not eligible for scheduling: cupsd will print the pending jobs behind an
    old held job while that job goes on waiting for a person. Ordering it by
    age would tell someone they are 3rd in line when they are actually next —
    a page whose only real claim is "here is your place" cannot get that
    wrong."""
    return (not job.is_printing, job.is_held, -job.priority, job.cups_job_id)
