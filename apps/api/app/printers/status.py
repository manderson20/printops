"""Derives PrintOps's online/error/offline status from a printer's raw IPP
printer-state* attributes, and refreshes a Printer row with the result.

Used both by the 60s background poll (app/main.py) and the manual
POST /printers/{id}/check-status endpoint (app/routers/printers.py).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.models.printer import Printer
from app.printers import cups_health, job_control, network_health, queue_recovery, queue_stall
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
# How many consecutive failed probes it takes before a printer is called
# offline. Two, because one is a timeout and two in a row a minute apart is
# a printer — see refresh_printer_status.
CONSECUTIVE_PROBE_FAILURES_BEFORE_OFFLINE = 2

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


async def refresh_printer_status(printer: Printer, *, manual: bool = False) -> None:
    """Probes `printer` over IPP and updates its status fields in place.
    Does not commit — the caller owns the transaction (matches
    app/printers/queue_sync.py's convention).

    `manual` marks a check a person asked for (the "Check Status" button),
    which skips the recovery cooldown below — same rule as
    auto_resync_due.

    One failed probe is not offline.

    The trigger for this was the LCACTC RM 502 (an HP M652). Measured
    2026-08-23: it loses about 18% of packets sent to it, in blackouts of
    almost exactly 7 seconds arriving almost exactly 37 seconds apart, and it
    does this under continuous once-a-second traffic, so it is not the device
    sleeping between requests. Its own interface counters are clean — link up
    at 1 Gbps, no flap in three days, zero errors and zero discards in either
    direction — which says the traffic never reaches it. The cause is on the
    network path and is not diagnosed yet; a printer on the same subnet lost
    nothing across 51 samples.

    The probe usually survives a blackout on TCP retransmits, and when the
    retransmit ladder overshoots STATE_TIMEOUT_SECONDS the printer flips to
    offline and back — with a capability rediscovery and a queue resync on
    every return trip. Nothing was wrong with the printer on any of them.

    So a miss is counted rather than acted on, and only
    CONSECUTIVE_PROBE_FAILURES_BEFORE_OFFLINE in a row change the status. A
    printer that has genuinely gone away misses every time and is reported
    within two cycles; a sleeping one almost never misses twice running. The
    debounce applies to a manual check too — the button asks the same question
    over the same 5 seconds, and a person clicking it deserves the same
    standard of evidence, not a faster wrong answer.
    """
    try:
        result: PrinterStateResult = await probe_printer_state(
            printer.ip_address,
            port=printer.port,
            tls=printer.use_tls,
            ipp_path=printer.effective_ipp_path,
        )
    except PrinterProbeError as exc:
        answered = False
        printer.status_probe_failures = (printer.status_probe_failures or 0) + 1
        if printer.status_probe_failures >= CONSECUTIVE_PROBE_FAILURES_BEFORE_OFFLINE:
            printer.status = "offline"
            printer.status_reasons = None
            printer.status_message = str(exc)
    else:
        answered = True
        printer.status_probe_failures = 0
        status, message = derive_status(result.printer_state, result.state_reasons)
        printer.status = status
        printer.status_reasons = [r for r in result.state_reasons if r != "none"] or None
        printer.status_message = result.state_message or message
    printer.status_checked_at = datetime.now(UTC)
    # Recorded for every probe, including the misses that never reach the
    # offline threshold above. Those are exactly the evidence the debounce
    # exists to stop acting on, and exactly what says the path to a printer is
    # dropping traffic (app/printers/network_health.py).
    flap = network_health.observe(str(printer.id), answered)
    # Both of the steps below used to read `status` as "what the probe just
    # found", which it no longer is: the debounce lets it lag a cycle behind.
    # They are told what this probe actually did instead, or the first missed
    # probe on a still-"online" row would resume a stopped queue and start
    # timing a stall for a device that had just failed to answer.
    if await _apply_queue_recovery(printer, manual=manual, device_answered=answered):
        # A queue that cupsd had stopped explains everything the stall
        # detector would otherwise report, and says it more usefully. Running
        # both would bury the real diagnosis under "check the port/TLS/IPP
        # path", which is precisely the wrong place to send someone whose
        # printer was simply away being serviced.
        return
    if not answered:
        # The stall clock still has to be cleared, which _apply_queue_stall
        # used to do on this path: before the debounce a failed probe set the
        # status to offline, and its first act for a non-online printer is to
        # forget the head-job observation. Returning without that left the
        # observation standing for the whole outage, so the first successful
        # probe after 30 minutes away reported the unchanged head job as a
        # stalled queue — on a printer that had just come back, and with an
        # "error" status that stops refresh_printer_status_and_rediscover
        # doing the reconnect discovery and resync that would get it moving.
        queue_stall.forget(str(printer.id))
        return
    await _apply_queue_stall(printer)
    _apply_network_flap(printer, flap)


async def _apply_queue_recovery(
    printer: Printer, *, manual: bool = False, device_answered: bool = True
) -> bool:
    """Starts this printer's CUPS queue again if cupsd had stopped it, and
    reports the fact either way. Returns whether the queue was found stopped
    — i.e. whether this is now the printer's diagnosis.

    The device having gone away and come back is the ordinary case, not an
    exotic one: a copier is wheeled off for service, cupsd stops its queue
    when a job fails mid-delivery, and the copier comes back fine. Nothing
    used to notice, because every other signal PrintOps collects describes
    the device rather than the queue — see app/printers/queue_recovery.py.

    A queue is only resumed when the device itself is reporting healthy. If
    the printer is still offline or in error, resuming would hand cupsd one
    more job to fail and get the queue stopped again; the device's own
    message is the better diagnosis, so it is kept and the paused queue is
    recorded alongside it as a reason.

    `device_answered` is that same rule applied to the probe this cycle rather
    than to the stored status, which since the offline debounce can still read
    "online" one missed probe after the device stopped answering. A printer
    that did not answer is not reporting healthy whatever the row says, and
    resuming its queue on the strength of a stale value is the exact thing the
    rule above exists to prevent."""
    printer_id = str(printer.id)

    # Same cupsd-slot reasoning as _apply_queue_stall below: this adds one
    # short-lived lpstat per printer per cycle, and a scheduler with no room
    # left is not the moment to spend slots on maintenance. Skipping is safe
    # — a stopped queue stays stopped, and the next uncongested cycle finds
    # it.
    if cups_health.is_saturated():
        return False

    state = await asyncio.to_thread(queue_recovery.local_queue_state, printer_id)
    if state is None or not state.stopped:
        # Includes "couldn't ask cupsd" and "this printer has no queue": in
        # neither case is there a recovery in progress to remember.
        queue_recovery.forget(printer_id)
        return False

    # The stall clock measures how long the head job has sat there, which on a
    # stopped queue is only a measure of how long the queue was off. Left
    # standing, it would fire the moment the queue restarts — reporting a
    # four-hour stall against a job that is at that moment printing normally,
    # with the port/TLS/IPP-path advice attached. Restart the clock instead.
    queue_stall.forget(printer_id)

    if not device_answered or printer.status != "online":
        # Deliberately still checked every cycle, and deliberately not
        # resumed. A stopped queue keeps *accepting* jobs, so they queue up
        # behind it and print when it starts again — which is the behaviour
        # wanted while a printer is away, rather than handing cupsd one more
        # job to fail every minute.
        if printer.status != "online":
            # Only a printer the debounce has actually called offline counts
            # as away. One missed probe blocks this cycle's resume, but must
            # not arm this bookkeeping: note_device_away pairs with
            # note_device_back, which discards the whole backoff once the
            # device reads online for five minutes. A lossy printer that never
            # left would otherwise clear its four-hour cooldown every time it
            # dropped a single probe, and be handed another job to fail.
            queue_recovery.note_device_away(printer_id)
            queue_recovery.note_still_stopped(printer_id)
        printer.status_reasons = [
            *(printer.status_reasons or []),
            queue_recovery.QUEUE_PAUSED_REASON,
        ]
        return False

    # Before the cooldown check, not after: a device that has come back and
    # stayed back clears the backoff it earned while it was away, so a printer
    # returning from service is resumed on the next poll instead of waiting
    # out a timer of up to four hours with its users' jobs behind it.
    queue_recovery.note_device_back(printer_id)

    if not (manual or queue_recovery.resume_due(printer_id)):
        queue_recovery.note_still_stopped(printer_id)
        _mark_paused(printer, state, resumed=False)
        return True

    try:
        await asyncio.to_thread(queue_recovery.resume_queue, printer_id)
    except queue_recovery.QueueResumeError as exc:
        logger.warning("Could not restart the CUPS queue for %s: %s", printer.name, exc)
        _mark_paused(printer, state, resumed=False)
        return True

    logger.warning(
        "%s: CUPS had stopped this printer's queue (%s) — restarted it.",
        printer.name,
        state.message or "no reason given",
    )
    _mark_paused(printer, state, resumed=True)
    return True


def _mark_paused(printer: Printer, state: queue_recovery.LocalQueueState, *, resumed: bool) -> None:
    """Records a stopped queue on the printer row.

    A queue that was just restarted is left reading `online` with no reason
    keyword: the fault is over, the message says what happened, and the UI
    renders every status reason as a red badge (apps/web .../printers/[id]).
    A red badge on a printer that is working again is an error nobody can
    act on, and those train people to ignore the field. A queue still
    stopped keeps both — there it is exactly what an admin should see."""
    printer.status_message = queue_recovery.paused_reason(state, resumed)
    if resumed:
        return
    printer.status = "error"
    printer.status_reasons = [
        *(printer.status_reasons or []),
        queue_recovery.QUEUE_PAUSED_REASON,
    ]


async def _apply_queue_stall(printer: Printer) -> None:
    """Downgrades an apparently-healthy printer whose queue has stopped moving.

    Only applied to printers that look online: a printer already reporting
    offline or error has told us something more specific, and burying that
    under a stall message would lose the better diagnosis. The case this exists
    for is precisely the one where every other signal says the printer is fine
    (app/printers/queue_stall.py)."""
    if printer.status != "online":
        queue_stall.forget(str(printer.id))
        return

    # Reading the queue costs a cupsd client slot, and this runs for every
    # online printer on the 60s poll — a standing load cupsd did not carry
    # before. Detecting a stalled queue is not worth helping exhaust the
    # MaxClients pool that cups_health exists to protect: a saturated
    # scheduler already looks to users exactly like a printer that has
    # stopped responding, which is the very failure this is meant to report.
    # State is dropped rather than held, so the clock restarts once there is
    # room again and a gap in observation can't be mistaken for a stall.
    if cups_health.is_saturated():
        queue_stall.forget(str(printer.id))
        return

    snapshot = await asyncio.to_thread(job_control.queue_snapshot, str(printer.id))
    stuck_for = queue_stall.observe(str(printer.id), snapshot)
    if stuck_for is None or snapshot is None:
        return

    reason = queue_stall.stall_reason(stuck_for, snapshot)
    printer.status = "error"
    printer.status_message = reason
    printer.status_reasons = [*(printer.status_reasons or []), "printops-queue-stalled"]
    logger.warning("%s: %s", printer.name, reason)


def _apply_network_flap(printer: Printer, flap: network_health.NetworkFlap | None) -> None:
    """Reports a printer that is answering, but missing enough status checks
    that the path to it is the thing worth looking at.

    Left reading `online`, and that is the whole judgement here. The printer is
    up and printing; calling it "error" would put a red badge on a working
    machine, and those are what teach people to stop reading the field — the
    same reasoning _mark_paused uses when it declines to leave a badge on a
    queue that has just been fixed. What is faulty is the path, not the device,
    so it gets its own keyword and the UI says so in its own tone.

    Not applied over a more specific diagnosis: a stopped or stalled queue has
    already given an admin something to act on directly, and a lossy path is
    background next to either."""
    if flap is None or printer.status != "online":
        return
    reason = network_health.flap_reason(flap)
    printer.status_message = reason
    printer.status_reasons = [
        *(printer.status_reasons or []),
        network_health.NETWORK_UNSTABLE_REASON,
    ]
    logger.warning("%s: %s", printer.name, reason)


async def refresh_printer_status_and_rediscover(printer: Printer, *, manual: bool = False) -> None:
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
    await refresh_printer_status(printer, manual=manual)
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

    Four gates. Three were learned from one flapping copier that resynced 137
    times in a day and took the whole scheduler down with it (see
    app/printers/cups_health.py): a per-printer cooldown that backs off on
    repeated failure, a check that cupsd has slots to spare, and a
    process-wide lock so a network blip can't start a dozen at once.

    The fourth is that a queue with work on it is left alone. Syncing runs
    `lpadmin`, and cupsd restarts every job on a queue whose printer is
    modified — including jobs it has itself just given up on. On the LCACTC
    Kyocera (2026-08-20) that closed a loop: a job stalled, cupsd cancelled it
    after its 3-hour limit, the next automatic resync 30 seconds later
    restarted it, and it stalled again, for six hours. Nothing behind it in
    the queue ever printed.

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

    active = await asyncio.to_thread(job_control.active_job_count, str(printer.id))
    if active is None or active > 0:
        # Same reasoning as the saturation gate — nothing was attempted, so
        # no cooldown is stamped and the resync happens on a later cycle once
        # the queue has drained. `None` (couldn't ask cupsd) counts as busy:
        # the whole point is to not modify a queue that might have work on it,
        # and an unanswered lpstat is not evidence that it doesn't.
        logger.debug(
            "Skipping automatic queue resync for %s — %s.",
            printer.name,
            "could not determine queue depth" if active is None else f"{active} job(s) queued",
        )
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
