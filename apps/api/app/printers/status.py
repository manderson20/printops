"""Derives PrintOps's online/error/offline status from a printer's raw IPP
printer-state* attributes, and refreshes a Printer row with the result.

Used both by the 60s background poll (app/main.py) and the manual
POST /printers/{id}/check-status endpoint (app/routers/printers.py).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.models.printer import Printer
from app.printers import cups_health
from app.printers.discovery import refresh_printer_capabilities
from app.printers.ipp_client import PrinterProbeError, PrinterStateResult, probe_printer_state
from app.printers.queue_sync import QueueSyncError, sync_queue

logger = logging.getLogger(__name__)

# IPP printer-state values (RFC 8011 §5.4.12).
PRINTER_STATE_STOPPED = 5

# How long after an automatic queue resync before this printer may have
# another one. A printer that genuinely comes back after being switched off
# overnight is well past this and resyncs immediately; a printer flapping
# every few minutes gets one resync and then waits.
AUTO_RESYNC_COOLDOWN = timedelta(minutes=30)

# Each consecutive failure doubles the wait, because a resync that just
# failed is the least likely to succeed if repeated at once — and it is
# exactly the failing ones that are expensive (a full `lpadmin -m
# everywhere` against an unresponsive device blocks a cupsd slot for its
# whole 30-second timeout).
MAX_AUTO_RESYNC_BACKOFF = timedelta(hours=6)

# Enough doublings to reach the cap from the base cooldown, and no more.
_MAX_BACKOFF_DOUBLINGS = 16

# Automatic resyncs run one at a time process-wide. The 60s status loop
# probes every printer concurrently, so without this a network blip that
# nudges a dozen printers online at once would fire a dozen simultaneous
# lpadmin storms at the same scheduler.
_AUTO_RESYNC_LOCK = asyncio.Lock()


def _auto_resync_backoff(printer: Printer) -> timedelta:
    failures = printer.auto_queue_sync_failures or 0
    if failures <= 0:
        return AUTO_RESYNC_COOLDOWN
    # Clamped before the shift, not after: a printer that has been failing
    # for weeks reaches four figures, and 2**that overflows rather than
    # merely being large.
    doublings = min(failures, _MAX_BACKOFF_DOUBLINGS)
    return min(AUTO_RESYNC_COOLDOWN * (2**doublings), MAX_AUTO_RESYNC_BACKOFF)


def auto_resync_due(printer: Printer, now: datetime | None = None) -> bool:
    """Whether this printer's automatic queue resync is off cooldown.

    Only automatic resyncs are rate-limited. A person clicking "Resync
    Queue" is answering for the consequences themselves and is never made
    to wait."""
    now = now or datetime.now(UTC)
    last = printer.last_auto_queue_sync_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last >= _auto_resync_backoff(printer)


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
    (app/printers/discovery.py) and retries the CUPS queue sync
    (app/printers/queue_sync.py) if the printer just came back online — a
    device can be physically swapped, gain/lose a module (finisher, extra
    tray), or (confirmed live: MS - Cletus Copier) have had its queue built
    from CUPS's generic degraded-capability fallback PPD because it was
    offline the first time its queue was synced. None of that should require
    someone remembering to click "Rediscover"/"Resync Queue" once the
    printer is back. Used by both the 60s background loop and the manual
    check-status endpoint (app/main.py, app/routers/printers.py) so the two
    behave identically."""
    was_online = printer.status == "online"
    await refresh_printer_status(printer)
    if printer.status != "online" or was_online:
        return
    # Capability discovery is one IPP probe and stays unconditional; it is
    # the queue resync behind it that has to be rationed.
    await refresh_printer_capabilities(printer)
    await run_automatic_queue_sync(printer)


async def run_automatic_queue_sync(printer: Printer) -> bool:
    """Resync this printer's CUPS queue on PrintOps' own initiative, unless
    doing it now would cost more than it's worth. Returns whether a sync
    was actually attempted.

    Three gates, each learned from one flapping copier that resynced 137
    times in a day and took the whole scheduler down with it (see
    app/printers/cups_health.py): a per-printer cooldown that backs off on
    repeated failure, a check that cupsd has slots to spare, and a
    process-wide lock so a network blip can't start a dozen at once.

    A skipped resync costs a stale PPD until the next transition. An
    unskipped one cost the district an afternoon of printing."""
    if not auto_resync_due(printer):
        logger.debug(
            "Skipping automatic queue resync for %s — still within its cooldown "
            "(%s consecutive failures).",
            printer.name,
            printer.auto_queue_sync_failures or 0,
        )
        return False
    if cups_health.is_saturated():
        # Deliberately not stamped as an attempt: nothing was tried, and a
        # printer shouldn't serve a cooldown for the scheduler's state.
        return False

    async with _AUTO_RESYNC_LOCK:
        printer.last_auto_queue_sync_at = datetime.now(UTC)
        try:
            await asyncio.to_thread(sync_queue, str(printer.id))
            printer.queue_sync_error = None
            printer.auto_queue_sync_failures = 0
        except QueueSyncError as exc:
            printer.queue_sync_error = str(exc)
            printer.auto_queue_sync_failures = (printer.auto_queue_sync_failures or 0) + 1
            logger.warning(
                "Automatic queue resync failed for %s (%s in a row): %s",
                printer.name,
                printer.auto_queue_sync_failures,
                exc,
            )
    return True
