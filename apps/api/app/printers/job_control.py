import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
CANCEL_SCRIPT = SCRIPTS_DIR / "cancel_cups_job.sh"
PURGE_SCRIPT = SCRIPTS_DIR / "purge_cups_queue.sh"

CANCEL_TIMEOUT_SECONDS = 10
PURGE_TIMEOUT_SECONDS = 15


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


def purge_cups_queue(printer_id: str) -> None:
    """Cancels every job queued on this printer's CUPS queue. Raises
    JobControlError on failure — see cancel_cups_job for why this isn't
    treated as best-effort."""
    _run(PURGE_SCRIPT, printer_id, PURGE_TIMEOUT_SECONDS)
