import getpass
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.parsers.expat import ExpatError

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
CANCEL_SCRIPT = SCRIPTS_DIR / "cancel_cups_job.sh"
PURGE_SCRIPT = SCRIPTS_DIR / "purge_cups_queue.sh"
PRIORITY_SCRIPT = SCRIPTS_DIR / "set_cups_job_priority.sh"
HOLD_SCRIPT = SCRIPTS_DIR / "hold_cups_job.sh"

CANCEL_TIMEOUT_SECONDS = 10
PRIORITY_TIMEOUT_SECONDS = 10
HOLD_TIMEOUT_SECONDS = 10
PURGE_TIMEOUT_SECONDS = 15
LPSTAT_TIMEOUT_SECONDS = 5
IPPTOOL_TIMEOUT_SECONDS = 10

# IPP job-state values (RFC 8011 §5.3.7). Named because the numbers are
# meaningless at the call site and the difference between 7 and 8 decides
# whether a job is recorded as cancelled or failed.
JOB_STATE_PENDING = 3
JOB_STATE_PENDING_HELD = 4
JOB_STATE_PROCESSING = 5
JOB_STATE_PROCESSING_STOPPED = 6
JOB_STATE_CANCELED = 7
JOB_STATE_ABORTED = 8
JOB_STATE_COMPLETED = 9

# The job is still cupsd's to finish — nothing to reconcile, whatever the
# database says. "Processing-stopped" belongs here: that is a job waiting on a
# queue cupsd stopped, which app/printers/queue_recovery.py restarts.
JOB_STATES_IN_FLIGHT = (
    JOB_STATE_PENDING,
    JOB_STATE_PENDING_HELD,
    JOB_STATE_PROCESSING,
    JOB_STATE_PROCESSING_STOPPED,
)

_COMPLETION_ATTRIBUTES = (
    "job-state",
    "job-state-reasons",
    "job-media-sheets-completed",
    "sides",
    "output-mode",
    "print-color-mode",
    "media",
)


class JobControlError(Exception):
    pass


def _run(script: Path, arg: str, timeout: int) -> None:
    _run_args(script, [arg], timeout)


def _run_args(script: Path, args: list[str], timeout: int) -> None:
    try:
        result = subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise JobControlError(f"{script.name} not found on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise JobControlError(f"{script.name} timed out after {timeout}s.") from exc

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        raise JobControlError(reason or f"{script.name} exited {result.returncode}.")


@dataclass(frozen=True)
class CupsJobIdentity:
    """Who cupsd thinks a job id currently belongs to."""

    uuid: str | None
    owner: str | None


def cups_job_identity(printer_id: str, cups_job_id: int) -> CupsJobIdentity | None:
    """Asks cupsd whose job a given id is right now, or None if it has no such
    job on that queue.

    CUPS job ids are not permanent: they restart from 1 when the spool is
    cleared, so a `jobs` row from before a reset can name an id that now
    belongs to somebody else's document. `cancel` takes only the number, which
    makes acting on a stale row a way to cancel a stranger's print — see
    cancel_job in app/routers/jobs.py, which checks this before cancelling
    anything it did not watch go out.

    requesting-user-name is named for the same reason as in
    app/printers/print_queue.py: cupsd's default policy keeps
    job-originating-user-name private otherwise, and this comparison needs it.
    """
    request = (
        "{\n"
        "    OPERATION Get-Job-Attributes\n"
        "    GROUP operation-attributes-tag\n"
        "    ATTR charset attributes-charset utf-8\n"
        "    ATTR language attributes-natural-language en\n"
        f"    ATTR uri printer-uri ipp://localhost/printers/printops-{printer_id}\n"
        f"    ATTR integer job-id {cups_job_id}\n"
        f"    ATTR name requesting-user-name {getpass.getuser()}\n"
        "    ATTR keyword requested-attributes job-uuid,job-originating-user-name\n"
        "}\n"
    )
    output = _ipptool_plist(f"printops-{printer_id}", request)
    if output is None:
        return None
    if _plist_value(output, "StatusCode") != "successful-ok":
        return None
    return CupsJobIdentity(
        uuid=_plist_value(output, "job-uuid"),
        owner=_plist_value(output, "job-originating-user-name"),
    )


def cancel_cups_job(cups_job_id: int) -> None:
    """Cancels a single in-flight CUPS job. Raises JobControlError on
    failure — callers should surface this to the admin (unlike
    queue_sync.py's non-fatal convention, a cancel that silently didn't
    happen would be actively misleading)."""
    _run(CANCEL_SCRIPT, str(cups_job_id), CANCEL_TIMEOUT_SECONDS)


def set_cups_job_priority(cups_job_id: int, priority: int) -> None:
    """Moves one queued job up or down the line by changing its CUPS
    priority. Raises JobControlError on failure — unlike a cancel, a
    priority change that silently didn't happen leaves someone believing
    they have yielded when they are still at the head of the queue.

    See app/printers/print_queue.py for what the values mean and who is
    allowed to ask for this."""
    _run_args(PRIORITY_SCRIPT, [str(cups_job_id), str(priority)], PRIORITY_TIMEOUT_SECONDS)


def hold_cups_job(cups_job_id: int) -> None:
    """Holds one job where it is, so cupsd stops sending it and moves on to the
    next. Works on a job cupsd is printing as well as one that is waiting
    (confirmed against CUPS 2.4.16). Raises JobControlError: a hold that
    silently did not happen leaves the printer being sent the job it keeps
    failing on (app/printers/crash_guard.py)."""
    _run_args(HOLD_SCRIPT, [str(cups_job_id), "hold"], HOLD_TIMEOUT_SECONDS)


def release_cups_job(cups_job_id: int) -> None:
    """Lets a held job print. Raises JobControlError on failure."""
    _run_args(HOLD_SCRIPT, [str(cups_job_id), "resume"], HOLD_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class HeldCupsJob:
    """A job cupsd is holding on one of a printer's queues."""

    cups_job_id: int
    # "client" for printops-<id>, the queue people print to; "release" for
    # printops-release-<id>, which delivers held and Follow-Me jobs.
    queue: str
    document_name: str | None
    owner: str | None
    size_bytes: int | None
    submitted_at: datetime | None
    # cupsd's job-uuid. A job id is a spool position that comes round again
    # after the spool is cleared; this is what identifies the job — the same
    # value Job.cups_job_uuid records.
    job_uuid: str | None = None


def _held_jobs_request(queue_name: str) -> str:
    return (
        "{\n"
        "    OPERATION Get-Jobs\n"
        "    GROUP operation-attributes-tag\n"
        "    ATTR charset attributes-charset utf-8\n"
        "    ATTR language attributes-natural-language en\n"
        f"    ATTR uri printer-uri ipp://localhost/printers/{queue_name}\n"
        f"    ATTR name requesting-user-name {getpass.getuser()}\n"
        "    ATTR keyword which-jobs not-completed\n"
        "    ATTR boolean my-jobs false\n"
        "    ATTR keyword requested-attributes "
        "job-id,job-state,job-name,job-originating-user-name,job-k-octets,time-at-creation,job-uuid\n"
        "}\n"
    )


def _text(value: object) -> str | None:
    # cupsd sends names without a language, which ipptool renders as a plain
    # string; a name *with* one comes back as a dict.
    if isinstance(value, dict):
        value = value.get("string")
    return value if isinstance(value, str) and value else None


def _parse_held_jobs(output: str, queue: str) -> list[HeldCupsJob] | None:
    try:
        document = plistlib.loads(output.encode())
    except (plistlib.InvalidFileException, ExpatError, ValueError):
        return None
    tests = document.get("Tests") or []
    if not tests:
        return None
    status = tests[0].get("StatusCode")
    if status == "client-error-not-found":
        # No such queue: a virtual Follow-Me printer has no release queue.
        return []
    if status != "successful-ok":
        return None

    held = []
    for group in tests[0].get("ResponseAttributes") or []:
        cups_job_id = group.get("job-id")
        if not isinstance(cups_job_id, int) or group.get("job-state") != JOB_STATE_PENDING_HELD:
            continue
        kilobytes = group.get("job-k-octets")
        created = group.get("time-at-creation")
        held.append(
            HeldCupsJob(
                cups_job_id=cups_job_id,
                queue=queue,
                document_name=_text(group.get("job-name")),
                owner=_text(group.get("job-originating-user-name")),
                job_uuid=_text(group.get("job-uuid")),
                size_bytes=kilobytes * 1024 if isinstance(kilobytes, int) else None,
                submitted_at=datetime.fromtimestamp(created, UTC)
                if isinstance(created, int)
                else None,
            )
        )
    return held


def held_cups_jobs(printer_id: str) -> list[HeldCupsJob] | None:
    """Every job cupsd is holding on this printer's two queues, oldest job id
    first, or None if cupsd could not be asked.

    None is distinct from an empty list for the reason queue_snapshot gives: a
    question cupsd did not answer is not evidence that nothing is held."""
    held: list[HeldCupsJob] = []
    for queue_name, queue in (
        (f"printops-{printer_id}", "client"),
        (f"printops-release-{printer_id}", "release"),
    ):
        output = _ipptool_plist(queue_name, _held_jobs_request(queue_name))
        if output is None:
            return None
        jobs = _parse_held_jobs(output, queue)
        if jobs is None:
            return None
        held.extend(jobs)
    return sorted(held, key=lambda job: job.cups_job_id)


@dataclass(frozen=True)
class QueueSnapshot:
    """What is sitting on a printer's CUPS queue right now."""

    depth: int
    # The job at the head of the queue — the one that must finish before
    # anything behind it moves. None when the queue is empty.
    head_job: str | None
    # Size of that head job in bytes, when lpstat reported a usable one.
    # Callers use it to tell "legitimately enormous" from "wedged": at LCACTC
    # a 26 MB Photoshop file is ordinary traffic, and treating it the same as
    # a 60 KB memo produces either false alarms or blind spots.
    head_size_bytes: int | None = None


def queue_snapshot(printer_id: str) -> QueueSnapshot | None:
    """The current state of this printer's CUPS queue, or None if it couldn't
    be determined.

    Asks cupsd rather than reading the `jobs` table: a job only gets a row
    once PrintOps' backend has actually started running for it, so everything
    still waiting behind the job at the head of the queue is invisible in the
    database. During the LCACTC Kyocera outage (2026-08-20) three jobs sat
    queued and exactly one of them had a row.

    None (couldn't tell) is deliberately distinct from an empty queue —
    callers gate maintenance and stall detection on this, and "lpstat didn't
    answer" is not evidence that a queue is idle.
    """
    try:
        result = subprocess.run(
            ["lpstat", "-o", f"printops-{printer_id}"],
            capture_output=True,
            text=True,
            timeout=LPSTAT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return QueueSnapshot(depth=0, head_job=None)

    # lpstat lists oldest first: "<job-id> <user> <size-in-bytes> <date...>".
    # Only the first three fields are parsed — the remainder is a
    # locale-formatted timestamp not worth depending on.
    fields = lines[0].split()
    head = fields[0]
    size: int | None = None
    if len(fields) >= 3 and fields[2].isdigit():
        size = int(fields[2])
    return QueueSnapshot(depth=len(lines), head_job=head, head_size_bytes=size)


def active_job_count(printer_id: str) -> int | None:
    """How many jobs are queued or printing on this printer's CUPS queue, or
    None if that couldn't be determined."""
    snapshot = queue_snapshot(printer_id)
    return None if snapshot is None else snapshot.depth


def purge_cups_queue(printer_id: str) -> None:
    """Cancels every job queued on this printer's CUPS queue. Raises
    JobControlError on failure — see cancel_cups_job for why this isn't
    treated as best-effort."""
    _run(PURGE_SCRIPT, printer_id, PURGE_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class CupsJobOutcome:
    """What cupsd's own job record says became of a job PrintOps started.

    `state` is None when cupsd has no record of the job at all — which is not
    the same as a job that failed. cupsd keeps completed jobs in memory up to
    MaxJobs (500 by default) and then rolls the oldest off, so on a busy server
    a job's history is gone within a day or two while the row PrintOps wrote
    for it lives forever. Callers must decide what to do with "no record"
    themselves rather than reading it as an outcome.
    """

    state: int | None
    reason: str | None = None
    page_count: int | None = None
    color_mode: str | None = None
    duplex: bool | None = None
    paper_size: str | None = None

    @property
    def in_flight(self) -> bool:
        return self.state in JOB_STATES_IN_FLIGHT


def _ipptool_plist(queue_name: str, request: str) -> str | None:
    """Runs one ipptool request against the local cupsd and returns its plist
    output, or None if the command itself couldn't be run."""
    try:
        result = subprocess.run(
            ["ipptool", "-X", f"ipp://localhost/printers/{queue_name}", "/dev/stdin"],
            input=request,
            capture_output=True,
            text=True,
            timeout=IPPTOOL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # ipptool exits 0 for an IPP error status as readily as for success — the
    # status is in the plist, not the exit code, and reading it the other way
    # round would turn every not-found into "couldn't tell".
    return result.stdout or None


def _plist_value(output: str, key: str) -> str | None:
    # Single-valued attributes come back bare while multi-valued ones are
    # wrapped in <array>, the same quirk get_job_completion_attributes works
    # around in infra/cups/backends/printops.
    match = re.search(
        rf"<key>{re.escape(key)}</key>\s*(?:<array>\s*)?<(integer|string)>(.*?)</\1>",
        output,
    )
    return match.group(2) if match else None


def cups_job_outcome(printer_id: str, cups_job_id: int) -> CupsJobOutcome | None:
    """Asks the local cupsd what became of one job on a printer's queue.

    Returns None when the question went unanswered — cupsd couldn't be
    reached, or answered with an IPP error other than client-error-not-found.
    That is a distinct answer from CupsJobOutcome(state=None), which is cupsd
    answering that it has no record of this job. Neither is an outcome, and a
    caller that conflates them will eventually write off a job that is at that
    moment printing.

    Only the client-facing queue is asked. The internal release queue has no
    custom backend on it (app/printers/release.py), so it never produces the
    in-flight job rows this exists to resolve.
    """
    request = (
        "{\n"
        "    OPERATION Get-Job-Attributes\n"
        "    GROUP operation-attributes-tag\n"
        "    ATTR charset attributes-charset utf-8\n"
        "    ATTR language attributes-natural-language en\n"
        f"    ATTR uri printer-uri ipp://localhost/printers/printops-{printer_id}\n"
        f"    ATTR integer job-id {cups_job_id}\n"
        f"    ATTR keyword requested-attributes {','.join(_COMPLETION_ATTRIBUTES)}\n"
        "}\n"
    )
    output = _ipptool_plist(f"printops-{printer_id}", request)
    if output is None:
        return None

    status = _plist_value(output, "StatusCode")
    if status is None:
        return None
    if status == "client-error-not-found":
        # The ordinary case, and the only status that actually answers the
        # question with "no": the job is long gone from cupsd's history.
        return CupsJobOutcome(state=None)
    if status != "successful-ok":
        # Every other IPP error — not-authorized, a queue that isn't there, a
        # transient server-error — leaves the question unanswered. Reporting
        # that as state=None would say cupsd has no record of this job, and
        # _resolve (app/printers/job_reconcile.py) writes such a row off as an
        # unknown outcome once it is two hours old. That is the conflation
        # this function's own contract rules out: a job cupsd declined to
        # talk about may be printing right now. An unanswered question stays
        # unanswered, the sweep counts it as unasked, and it asks again.
        return None

    state_raw = _plist_value(output, "job-state")
    if state_raw is None or not state_raw.isdigit():
        return CupsJobOutcome(state=None)

    sheets = _plist_value(output, "job-media-sheets-completed")
    sides = _plist_value(output, "sides")
    color_raw = _plist_value(output, "output-mode") or _plist_value(output, "print-color-mode")
    return CupsJobOutcome(
        state=int(state_raw),
        reason=_plist_value(output, "job-state-reasons"),
        page_count=int(sheets) if sheets and sheets.isdigit() else None,
        color_mode=(
            None if color_raw is None else ("monochrome" if "monochrome" in color_raw else "color")
        ),
        duplex=None if sides is None else sides.startswith("two-sided"),
        paper_size=_plist_value(output, "media"),
    )
