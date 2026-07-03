import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
SYNC_SCRIPT = SCRIPTS_DIR / "sync_cups_queue.sh"
REMOVE_SCRIPT = SCRIPTS_DIR / "remove_cups_queue.sh"
SYNC_RELEASE_SCRIPT = SCRIPTS_DIR / "sync_release_queue.sh"
REMOVE_RELEASE_SCRIPT = SCRIPTS_DIR / "remove_release_queue.sh"

# -m everywhere probes the real printer over the network to build a driverless
# PPD — slower than a typical IPP capability probe, hence the longer timeout
# than app/printers/test_print.py's.
SYNC_TIMEOUT_SECONDS = 60
REMOVE_TIMEOUT_SECONDS = 20


class QueueSyncError(Exception):
    pass


def _run(script: Path, printer_id: str, timeout: int) -> None:
    try:
        result = subprocess.run(
            [str(script), printer_id],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise QueueSyncError(f"{script.name} not found on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise QueueSyncError(f"{script.name} timed out after {timeout}s.") from exc

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        raise QueueSyncError(reason or f"{script.name} exited {result.returncode}.")


def sync_queue(printer_id: str) -> None:
    """Creates/updates this printer's CUPS queue + AirPrint advertisement to
    match its current connection details. Raises QueueSyncError on failure
    — callers should record this non-fatally (see Printer.queue_sync_error),
    not block the printer create/update over it.

    Also syncs the internal direct-delivery release queue
    (app/printers/release.py) for every printer regardless of whether
    release is currently enabled — cheap, and avoids a separate
    create/remove lifecycle tied to toggling Printer.release_required."""
    _run(SYNC_SCRIPT, printer_id, SYNC_TIMEOUT_SECONDS)
    _run(SYNC_RELEASE_SCRIPT, printer_id, SYNC_TIMEOUT_SECONDS)


def remove_queue(printer_id: str) -> None:
    """Removes this printer's CUPS queue + AirPrint advertisement, and its
    internal release queue. Raises QueueSyncError on failure — callers
    should treat this as best-effort and not block the printer delete
    over it."""
    _run(REMOVE_SCRIPT, printer_id, REMOVE_TIMEOUT_SECONDS)
    _run(REMOVE_RELEASE_SCRIPT, printer_id, REMOVE_TIMEOUT_SECONDS)
