"""Reads back the CUPS queue's own current PageSize default — lets the
capabilities UI show "device says X, CUPS queue says Y" side by side, the
same diagnostic signal that identified the earlier *DefaultColorModel bug
(see scripts/sync_cups_queue.sh's ColorModel=RGB patch). Purely
informational: best-effort, never raises, since a missing/unsynced queue
just means nothing to compare yet rather than an error worth surfacing."""

import subprocess

TIMEOUT_SECONDS = 10


def get_cups_queue_default_page_size(printer_id: str) -> str | None:
    """Runs `lpoptions -p <queue> -l` and extracts the `*`-prefixed
    (default) PageSize choice, e.g. "Letter". Returns None on any failure
    (queue not synced yet, lpoptions missing, timeout, no PageSize line, or
    no starred choice) rather than raising."""
    queue_name = f"printops-{printer_id}"
    try:
        result = subprocess.run(
            ["lpoptions", "-p", queue_name, "-l"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        option, _, choices = line.partition(":")
        if not option.split("/", 1)[0].strip() == "PageSize":
            continue
        for choice in choices.split():
            if choice.startswith("*"):
                return choice[1:]
        return None

    return None
