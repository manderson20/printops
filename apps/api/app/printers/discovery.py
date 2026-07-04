"""Probes a printer's full IPP attribute set and updates its stored
capability fields — used on printer creation, via the manual "Rediscover"
button (POST /printers/{id}/discover), and by the offline->online
transition in app/main.py's status poll loop (a printer can be physically
swapped, or gain/lose a module like a finisher or extra tray, while it was
unreachable; re-probing on reconnect picks that up without waiting for
someone to notice and click Rediscover)."""

from datetime import UTC, datetime

from app.models.printer import Printer
from app.printers.capabilities import parse_capabilities, sanitize_raw_attributes
from app.printers.ipp_client import PrinterProbeError, probe_printer


async def refresh_printer_capabilities(printer: Printer) -> None:
    """Does not commit — the caller owns the transaction, matching
    app/printers/status.py's convention."""
    try:
        result = await probe_printer(
            printer.ip_address,
            port=printer.port,
            tls=printer.use_tls,
            ipp_path=printer.ipp_path,
        )
        printer.capabilities = parse_capabilities(result.raw_attributes)
        printer.capabilities_raw = sanitize_raw_attributes(result.raw_attributes)
        printer.capabilities_detected_at = datetime.now(UTC)
        printer.capabilities_error = None
        if printer.ipp_path is None:
            printer.ipp_path = result.resolved_path
        detected_model = printer.capabilities.get("make_model")
        if not printer.manufacturer and not printer.model and detected_model:
            printer.model = detected_model
    except PrinterProbeError as exc:
        printer.capabilities_error = str(exc)
