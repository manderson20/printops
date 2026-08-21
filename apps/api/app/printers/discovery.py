"""Probes a printer's full IPP attribute set and updates its stored
capability fields — used on printer creation, via the manual "Rediscover"
button (POST /printers/{id}/discover), and by the offline->online
transition in app/main.py's status poll loop (a printer can be physically
swapped, or gain/lose a module like a finisher or extra tray, while it was
unreachable; re-probing on reconnect picks that up without waiting for
someone to notice and click Rediscover)."""

import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.models.printer import Printer
from app.printers.capabilities import parse_capabilities, sanitize_raw_attributes
from app.printers.ipp_client import PrinterProbeError, ProbeResult, probe_printer

logger = logging.getLogger(__name__)

# Ports to assume when a redirect target names a scheme but no port.
_DEFAULT_PORT_FOR_SCHEME = {"ipp": 631, "ipps": 443, "http": 80, "https": 443}
_TLS_SCHEMES = {"ipps", "https"}


def _transport_from_redirect(location: str | None) -> tuple[int, bool, str] | None:
    """Reads (port, tls, path) out of a redirect target.

    A device that has been switched to TLS-only IPP answers its cleartext port
    with a redirect naming exactly where it now lives — scheme, port and path,
    all three. That is the device telling us its own configuration, and it is a
    better source than anyone guessing: on the LCACTC Kyocera (2026-08-20) the
    307 pointed at https://10.50.1.37:443/, which was precisely the setting
    someone otherwise had to work out by hand.

    Returns None if the target isn't usable, rather than guessing at a partial
    one — a wrong reconfiguration is worse than none.
    """
    if not location:
        return None
    parsed = urlsplit(location)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORT_FOR_SCHEME or not parsed.hostname:
        return None
    try:
        port = parsed.port or _DEFAULT_PORT_FOR_SCHEME[scheme]
    except ValueError:
        return None  # malformed port in the URL
    # An https:// redirect means "same service, over TLS" — the IPP path is
    # unchanged from what we asked for when the target names none.
    path = parsed.path or ""
    return port, scheme in _TLS_SCHEMES, path


async def _probe_following_redirect(printer: Printer) -> ProbeResult:
    """Probes the printer, and if the device redirects, retries at the address
    it named and adopts that as the printer's configuration.

    Adoption only happens once the new address has actually answered. The whole
    point is that CUPS cannot follow redirects, so believing one without
    verifying it would just move the printer to a second address that doesn't
    print either.
    """
    try:
        return await probe_printer(
            printer.ip_address,
            port=printer.port,
            tls=printer.use_tls,
            ipp_path=printer.effective_ipp_path,
        )
    except PrinterProbeError as exc:
        transport = _transport_from_redirect(exc.redirect.location if exc.redirect else None)
        if transport is None:
            raise
        port, tls, path = transport
        if (port, tls) == (printer.port, printer.use_tls):
            raise  # would retry the identical address and redirect again

        # Falsy rather than None: an empty path from the redirect target means
        # "it didn't say", so keep whatever the printer already had and let the
        # candidate-path walk settle it if that is blank too.
        result = await probe_printer(
            printer.ip_address, port=port, tls=tls, ipp_path=path or printer.effective_ipp_path
        )
        logger.info(
            "%s redirected to %s; adopting port=%s tls=%s path=%s after verifying it answers.",
            printer.name,
            exc.redirect.location if exc.redirect else "?",
            port,
            tls,
            result.resolved_path,
        )
        printer.port = port
        printer.use_tls = tls
        return result


async def refresh_printer_capabilities(printer: Printer) -> None:
    """Does not commit — the caller owns the transaction, matching
    app/printers/status.py's convention."""
    try:
        result = await _probe_following_redirect(printer)
        printer.capabilities = parse_capabilities(result.raw_attributes)
        printer.capabilities_raw = sanitize_raw_attributes(result.raw_attributes)
        printer.capabilities_detected_at = datetime.now(UTC)
        printer.capabilities_error = None
        # Always refreshed, and never written to `ipp_path`: this column is a
        # cache of what answered, so it has to be able to change when the device
        # does. Writing it into the override column instead is what let a stale
        # guess acquire tenure (see the 0061 migration).
        printer.ipp_path_detected = result.resolved_path
        # A device that only serves IPP over TLS is discovered via the upgrade
        # retry in app/printers/ipp_client.py even when use_tls is off. Persist
        # that, or the status poll (app/printers/status.py, which passes
        # printer.use_tls) keeps paying a wasted cleartext round trip against
        # this printer every cycle.
        if result.resolved_tls and not printer.use_tls:
            printer.use_tls = True
        detected_model = printer.capabilities.get("make_model")
        if not printer.manufacturer and not printer.model and detected_model:
            printer.model = detected_model
    except PrinterProbeError as exc:
        printer.capabilities_error = str(exc)
