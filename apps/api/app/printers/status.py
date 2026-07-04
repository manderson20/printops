"""Derives PrintOps's online/error/offline status from a printer's raw IPP
printer-state* attributes, and refreshes a Printer row with the result.

Used both by the 60s background poll (app/main.py) and the manual
POST /printers/{id}/check-status endpoint (app/routers/printers.py).
"""

from datetime import UTC, datetime

from app.models.printer import Printer
from app.printers.discovery import refresh_printer_capabilities
from app.printers.ipp_client import PrinterProbeError, PrinterStateResult, probe_printer_state

# IPP printer-state values (RFC 8011 §5.4.12).
PRINTER_STATE_STOPPED = 5


def derive_status(printer_state: int | None, state_reasons: list[str]) -> tuple[str, str | None]:
    """Maps a raw IPP state + state-reasons list to one of PrintOps's status
    values. "none" is IPP's own placeholder for "no reasons to report" — it's
    filtered out rather than treated as an actual reason. A reason ending in
    "-error" always means "error" regardless of the numeric state (some
    printers report state=4/processing even while jammed); otherwise a
    stopped queue (state=5) is also surfaced as "error" since that's the
    state operators care about, not just idle/processing."""
    reasons = [r for r in state_reasons if r != "none"]
    has_error_reason = any(r.endswith("-error") for r in reasons)
    if has_error_reason or printer_state == PRINTER_STATE_STOPPED:
        message = reasons[0] if reasons else None
        return "error", message
    if printer_state in (3, 4):
        return "online", (reasons[0] if reasons else None)
    return "unknown", None


async def refresh_printer_status(printer: Printer) -> None:
    """Probes `printer` over IPP and updates its status fields in place.
    Does not commit — the caller owns the transaction (matches
    app/printers/queue_sync.py's convention)."""
    try:
        result: PrinterStateResult = await probe_printer_state(
            printer.ip_address,
            port=printer.port,
            tls=printer.use_tls,
            ipp_path=printer.ipp_path,
        )
    except PrinterProbeError as exc:
        printer.status = "offline"
        printer.status_reasons = None
        printer.status_message = str(exc)
    else:
        status, message = derive_status(result.printer_state, result.state_reasons)
        printer.status = status
        printer.status_reasons = [r for r in result.state_reasons if r != "none"] or None
        printer.status_message = result.state_message or message
    printer.status_checked_at = datetime.now(UTC)


async def refresh_printer_status_and_rediscover(printer: Printer) -> None:
    """Refreshes status, then re-runs capability discovery
    (app/printers/discovery.py) if the printer just came back online — a
    device can be physically swapped, or gain/lose a module (finisher,
    extra tray), while it was unreachable for maintenance, and the change
    should surface without someone remembering to click "Rediscover" once
    it's back. Used by both the 60s background loop and the manual
    check-status endpoint (app/main.py, app/routers/printers.py) so the two
    behave identically, per check-status's own docstring."""
    was_online = printer.status == "online"
    await refresh_printer_status(printer)
    if printer.status == "online" and not was_online:
        await refresh_printer_capabilities(printer)
