import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class TestPrintError(Exception):
    pass


def _build_test_page(printer_name: str, username: str) -> bytes:
    """Minimal single-page PostScript doc — no external deps, and
    application/postscript is in every IPP Everywhere printer's PDL, unlike
    plain text which driverless queues often can't filter."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "PrintOps Test Print",
        f"Printer: {printer_name}",
        f"Triggered by: {username}",
        f"Time: {timestamp}",
        "If you can read this, the queue, proxy, and forwarding path all work.",
    ]
    body = "\n".join(
        f"72 {700 - i * 24} moveto ({line}) show" for i, line in enumerate(lines)
    )
    doc = f"""%!PS
/Helvetica findfont 14 scalefont setfont
{body}
showpage
"""
    return doc.encode()


def submit_test_print(printer_id: str, printer_name: str, username: str) -> str:
    """Submits a test page to the printer's CUPS queue via `lp`, so it goes
    through the exact same path (printops backend -> job logging -> real ipp
    backend) as a real job. Requires scripts/sync_cups_queue.sh to have been
    run for this printer already — raises TestPrintError with a clear reason
    otherwise."""
    queue_name = f"printops-{printer_id}"
    doc = _build_test_page(printer_name, username)

    with tempfile.NamedTemporaryFile(suffix=".ps", delete=False) as f:
        f.write(doc)
        path = Path(f.name)

    try:
        result = subprocess.run(
            ["lp", "-d", queue_name, "-U", username, "-t", "PrintOps Test Print", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise TestPrintError("The `lp` command isn't available on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TestPrintError("Submitting the test print timed out.") from exc
    finally:
        path.unlink(missing_ok=True)

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        if "Unknown destination" in reason or "does not exist" in reason:
            raise TestPrintError(
                "No CUPS queue exists for this printer yet — run "
                f"scripts/sync_cups_queue.sh {printer_id} on the print server first."
            )
        raise TestPrintError(reason or "lp exited with an error.")

    return result.stdout.strip()
