"""Notices when a printer's queue has stopped moving while the printer itself
still claims to be fine.

This is the layer that catches what the specific guards did not predict. On
2026-08-20 the LCACTC Kyocera was switched to TLS-only IPP; CUPS could no
longer deliver to it, but the device answered SNMP and its panel read "Ready.",
so every health signal PrintOps had was green. Three jobs sat in the queue for
six hours. The first anyone knew of it was a person saying a test print never
came out.

The signal that was available the whole time, and that nothing was watching, is
simply this: the job at the head of the queue never changed. That is cheap to
observe, needs no cooperation from the device, and does not care *why* delivery
is failing — which is the point, since the next cause will not be this one.

State is deliberately in-process and resets on restart: a stall that predates
the current process is not something this module can honestly claim to have
observed, and a false "stalled for 4 days" after a deploy would be worse than
saying nothing.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.printers.job_control import QueueSnapshot

logger = logging.getLogger(__name__)

# How long the same job may sit at the head of a queue before that queue is
# called stalled. Generous on purpose: this must not fire on a genuinely large
# job that is simply slow. Thirty minutes is far past "slow" for ordinary
# office traffic and well short of the six hours it took a human to notice on
# 2026-08-20.
STALL_THRESHOLD = timedelta(minutes=30)

# ...but "ordinary office traffic" is not what every printer sees. The LCACTC
# graphic-arts printer's normal workload is Photoshop output: the job at the
# centre of the 2026-08-20 incident was a 26 MB JPEG on 11x14 borderless media
# which, at 600dpi 8-bit colour, Ghostscript expands to a ~166 MB raster before
# a byte reaches the printer. Judging that by the same clock as a 60 KB memo
# gives a choice between crying wolf at a whole class's normal work and setting
# the threshold so high it never catches anything.
#
# So the head job's own size buys it proportionate extra grace. The constants
# are a defensible starting point rather than a measured optimum — one minute
# per megabyte, capped — and are worth revisiting once there is real data on
# how long large jobs on this hardware actually take.
STALL_GRACE_PER_MB = timedelta(minutes=1)
MAX_SIZE_GRACE = timedelta(minutes=60)
_BYTES_PER_MB = 1024 * 1024

# Below this, a job gets exactly the base threshold and no arithmetic. Scaling
# from zero would hand a 60 KB memo an extra three seconds — meaningless in
# itself, and it makes the rule harder to reason about than it needs to be
# ("why is this printer's threshold 30m03s?"). Grace is for jobs that are
# actually large.
SIZE_GRACE_FLOOR_BYTES = 5 * _BYTES_PER_MB


def threshold_for(size_bytes: int | None) -> timedelta:
    """How long a head job of this size is allowed to sit before its queue is
    called stalled."""
    if not size_bytes or size_bytes <= SIZE_GRACE_FLOOR_BYTES:
        return STALL_THRESHOLD
    grace = min(STALL_GRACE_PER_MB * (size_bytes / _BYTES_PER_MB), MAX_SIZE_GRACE)
    return STALL_THRESHOLD + grace


@dataclass
class _HeadObservation:
    head_job: str
    first_seen: datetime


_observations: dict[str, _HeadObservation] = {}


def reset() -> None:
    """Drops all remembered state. For tests, and for callers that know the
    world has changed underneath them (e.g. a queue was purged)."""
    _observations.clear()


def forget(printer_id: str) -> None:
    _observations.pop(printer_id, None)


def observe(
    printer_id: str, snapshot: QueueSnapshot | None, now: datetime | None = None
) -> timedelta | None:
    """Records this poll's queue state and returns how long the queue has been
    stuck on the same head job, or None if it isn't stuck.

    Returning a duration rather than a bool keeps the decision about what counts
    as "too long" with the caller, and lets the message say how long it has been
    — which is the difference between an alert someone acts on and one they
    dismiss.
    """
    now = now or datetime.now(UTC)

    # Unknown queue state must not be mistaken for progress *or* for a stall:
    # forget what we had and start again next poll. Otherwise a few failed
    # lpstat calls during a cupsd restart would look like a queue that never
    # moved.
    if snapshot is None or snapshot.head_job is None:
        _observations.pop(printer_id, None)
        return None

    previous = _observations.get(printer_id)
    if previous is None or previous.head_job != snapshot.head_job:
        # A different job at the head means the queue moved — that is the
        # definition of progress here, and it deliberately does not depend on
        # the queue getting shorter (a busy printer stays deep all day).
        _observations[printer_id] = _HeadObservation(head_job=snapshot.head_job, first_seen=now)
        return None

    stuck_for = now - previous.first_seen
    return stuck_for if stuck_for >= threshold_for(snapshot.head_size_bytes) else None


def stall_reason(stuck_for: timedelta, snapshot: QueueSnapshot) -> str:
    """The operator-facing explanation. Says what was observed and what it
    usually means, without asserting a cause it cannot know."""
    minutes = int(stuck_for.total_seconds() // 60)
    job_word = "job" if snapshot.depth == 1 else "jobs"
    size = ""
    if snapshot.head_size_bytes:
        # Named because it is the first thing worth knowing: a queue blocked
        # behind something enormous is a different problem from one blocked
        # behind nothing in particular.
        size = f" The job at the head is {snapshot.head_size_bytes / _BYTES_PER_MB:.1f} MB."
    return (
        f"Queue has not moved for {minutes} minutes ({snapshot.depth} {job_word} waiting) "
        "even though the printer reports itself reachable. Jobs are reaching CUPS but not "
        "printing — check the printer's connection settings (port/TLS/IPP path) and the "
        f"device's own job list.{size}"
    )
