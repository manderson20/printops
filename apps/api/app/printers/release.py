import subprocess


class ReleaseError(Exception):
    pass


def parse_job_options(raw: str | None) -> list[str]:
    """CUPS's job-options string (Job.held_job_options) is already
    space-separated `key=value` tokens — the same shape `lp -o` expects,
    so each token is replayed straight through rather than re-deriving
    individual flags. Tokens without an "=" (shouldn't happen, but CUPS's
    own format isn't a strict grammar we control) are dropped rather than
    passed to `lp` malformed."""
    if not raw:
        return []
    return [token for token in raw.split() if "=" in token]


def submit_released_job(
    printer_id: str,
    held_file_path: str,
    document_name: str | None,
    copy_count: int | None,
    held_job_options: str | None,
) -> str:
    """Delivers a previously-held job via the printer's internal
    direct-delivery queue (scripts/sync_release_queue.sh) — mirrors
    app/printers/test_print.py's `lp -d <queue>` pattern, the same
    "shell out to lp" approach already proven working in this codebase.
    Raises ReleaseError on failure (queue missing, lp not found, timeout,
    or lp itself reporting an error).

    Note: unlike a normal (non-held) job, this delivery doesn't go through
    our own CUPS backend script (infra/cups/backends/printops) — the
    release queue is a plain CUPS queue with no custom backend attached.
    A successful return here means "handed to CUPS for delivery", not
    "confirmed physically printed" — the post-completion ipptool
    page_count/color_mode/duplex capture normal jobs get isn't available
    for released jobs.
    """
    queue_name = f"printops-release-{printer_id}"
    argv = ["lp", "-d", queue_name]
    if copy_count:
        argv += ["-n", str(copy_count)]
    if document_name:
        argv += ["-t", document_name]
    for option in parse_job_options(held_job_options):
        argv += ["-o", option]
    argv.append(held_file_path)

    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise ReleaseError("The `lp` command isn't available on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError("Releasing the job timed out.") from exc

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        if "Unknown destination" in reason or "does not exist" in reason:
            raise ReleaseError(
                "No internal release queue exists for this printer yet — run "
                f"scripts/sync_release_queue.sh {printer_id} on the print server first."
            )
        raise ReleaseError(reason or "lp exited with an error.")

    return result.stdout.strip()
