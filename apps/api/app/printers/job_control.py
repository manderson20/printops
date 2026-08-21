import subprocess
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
CANCEL_SCRIPT = SCRIPTS_DIR / "cancel_cups_job.sh"
PURGE_SCRIPT = SCRIPTS_DIR / "purge_cups_queue.sh"

CANCEL_TIMEOUT_SECONDS = 10
PURGE_TIMEOUT_SECONDS = 15
LPSTAT_TIMEOUT_SECONDS = 5


class JobControlError(Exception):
    pass


def _run(script: Path, arg: str, timeout: int) -> None:
    try:
        result = subprocess.run(
            [str(script), arg],
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


def cancel_cups_job(cups_job_id: int) -> None:
    """Cancels a single in-flight CUPS job. Raises JobControlError on
    failure — callers should surface this to the admin (unlike
    queue_sync.py's non-fatal convention, a cancel that silently didn't
    happen would be actively misleading)."""
    _run(CANCEL_SCRIPT, str(cups_job_id), CANCEL_TIMEOUT_SECONDS)


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
