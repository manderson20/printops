import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jwt import PyJWTError
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db import AsyncSessionLocal
from app.integrations.git_update import get_current_version
from app.integrations.google_workspace import GoogleWorkspaceError
from app.integrations.google_workspace import run_sync as run_google_workspace_sync
from app.integrations.mosyle import MosyleError
from app.integrations.mosyle import run_sync as run_mosyle_sync
from app.models.google_workspace import GoogleWorkspaceSettings
from app.models.job import Job
from app.models.mosyle import MosyleSettings
from app.models.printer import Printer
from app.models.report import PrinterTonerReading
from app.models.snmp import PrinterCounterReading
from app.models.syslog import PrinterSyslogEvent
from app.printers.discovery import refresh_printer_capabilities
from app.printers.queue_sync import QueueSyncError, sync_queue
from app.printers.snmp_counters import (
    SnmpProbeError,
    get_or_create_snmp_defaults,
    record_reading,
    refresh_printer_counters,
    resolve_snmp_config,
    sync_toner_levels,
)
from app.printers.status import refresh_printer_status_and_rediscover
from app.routers import (
    attribution_aliases,
    auth,
    copier_imports,
    copier_unmapped,
    device_overrides,
    health,
    internal,
    jobs,
    mfp_devices,
    printers,
    quota_holds,
    release,
    reports,
    self_service_print,
    staff_copier_identities,
    updates,
    users,
    zabbix_integration,
)
from app.routers import settings as settings_router
from app.routers import syslog as syslog_router
from app.syslog.service import get_or_create_syslog_settings

settings = get_settings()
logger = logging.getLogger(__name__)

DEVICE_SYNC_INTERVAL_SECONDS = 15 * 60


def _make_device_sync_loop(name: str, settings_model, run_sync_fn, error_cls: type[Exception]):
    """Builds a periodic device-cache sync loop for a device→user
    integration (Mosyle, Google Workspace, ...) — refreshed periodically
    so per-job attribution lookups (app/attribution/resolve.py) never make
    a live API call. Runs forever until cancelled at shutdown; a sync
    failure just logs and retries next interval rather than crashing."""

    async def _loop() -> None:
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(settings_model.enabled).limit(1))
                    if result.scalar_one_or_none():
                        await run_sync_fn(db)
            except error_cls as exc:
                logger.warning("%s device sync failed: %s", name, exc)
            except Exception:
                logger.exception("Unexpected error in %s device sync loop", name)
            await asyncio.sleep(DEVICE_SYNC_INTERVAL_SECONDS)

    return _loop


PRINTER_STATUS_POLL_INTERVAL_SECONDS = 60


async def _printer_status_poll_loop() -> None:
    """Refreshes every printer's online/error/offline status (see
    app/printers/status.py) once a minute — frequent enough to catch a jam
    or an offline printer quickly without hammering the fleet. Each printer
    is probed independently (asyncio.gather + return_exceptions) so one
    unreachable printer can't stall/skip the rest of the cycle; a cycle-level
    failure (e.g. DB down) just logs and retries next interval.

    Also re-runs capability discovery and the CUPS queue sync whenever a
    printer transitions into "online" from anything else — see
    refresh_printer_status_and_rediscover's docstring."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                printers = (
                    (
                        await db.execute(
                            select(Printer).where(
                                Printer.archived_at.is_(None),
                                Printer.is_virtual.is_(False),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                async def _refresh_one(printer: Printer) -> None:
                    await refresh_printer_status_and_rediscover(printer)

                await asyncio.gather(*(_refresh_one(p) for p in printers), return_exceptions=True)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in printer status poll loop")
        await asyncio.sleep(PRINTER_STATUS_POLL_INTERVAL_SECONDS)


CAPABILITY_REDISCOVERY_INTERVAL_SECONDS = 30 * 60


async def _capability_rediscovery_loop() -> None:
    """Re-runs full capability discovery (app/printers/discovery.py) across
    every active printer every 30 minutes, independent of the status loop's
    offline->online-triggered rediscover above — a printer that's been
    online the whole time never otherwise gets re-probed, so a same-day
    change (a copier's tray reloaded with a different size, a finisher
    added) wouldn't show up in default_media_size/media_trays until someone
    happened to notice and click Rediscover, or the printer happened to
    blip offline. Same cadence and per-printer isolation as
    _snmp_counter_poll_loop below (asyncio.gather(..., return_exceptions=True)
    so one slow/unreachable device can't stall the rest of the fleet).

    If the freshly-probed default page size or per-tray contents differ
    from what was already stored, also re-runs the CUPS queue sync
    (app/printers/queue_sync.py) — otherwise this loop would only keep
    PrintOps's own display fresh while the actual CUPS queue's PPD (what
    an end user's print dialog reads as its default — see
    scripts/sync_cups_queue.sh's -m everywhere) stays a stale snapshot
    from whenever the queue was last created/resynced, drifting out of
    sync with the live device. Gated on an actual change, not resynced
    unconditionally every cycle: a full queue resync is much heavier (up
    to 90s, several sudo lpadmin/ipptool calls — see
    queue_sync.SYNC_TIMEOUT_SECONDS) than a plain capability probe, and
    regenerating every online printer's PPD every 30 minutes regardless
    of whether anything changed would be needless load on cupsd. Same
    non-fatal QueueSyncError handling as refresh_printer_status_and_rediscover
    (app/printers/status.py) uses for its own offline->online-triggered
    resync."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                printers = (
                    (
                        await db.execute(
                            select(Printer).where(
                                Printer.archived_at.is_(None),
                                Printer.is_virtual.is_(False),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                async def _refresh_one(printer: Printer) -> None:
                    previous = printer.capabilities or {}
                    previous_default = previous.get("default_media_size")
                    previous_trays = previous.get("media_trays")

                    await refresh_printer_capabilities(printer)

                    current = printer.capabilities or {}
                    media_changed = current.get("default_media_size") != previous_default or (
                        current.get("media_trays") != previous_trays
                    )
                    if media_changed:
                        try:
                            await asyncio.to_thread(sync_queue, str(printer.id))
                            printer.queue_sync_error = None
                        except QueueSyncError as exc:
                            printer.queue_sync_error = str(exc)

                await asyncio.gather(*(_refresh_one(p) for p in printers), return_exceptions=True)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in capability rediscovery loop")
        await asyncio.sleep(CAPABILITY_REDISCOVERY_INTERVAL_SECONDS)


SNMP_COUNTER_POLL_INTERVAL_SECONDS = 30 * 60


async def _snmp_counter_poll_loop() -> None:
    """Refreshes every printer's page/copy/print counters (see
    app/printers/snmp_counters.py) every 30 minutes — counters change far
    slower than reachability, and a poll costs up to 3 SNMP round-trips
    per printer, so this runs far less often than the 60s status loop
    (a single unreachable device can cost ~18s worst case with the poller's
    3s timeout/1 retry; wasteful to repeat that every minute across a
    fleet with several offline devices). Gated on the global
    SnmpDefaultsSettings.enabled flag — no-ops entirely until an admin
    turns SNMP polling on. Same per-printer isolation via
    asyncio.gather(..., return_exceptions=True) as the status loop.

    Also polls each printer's toner supply levels (sync_toner_levels) in
    the same per-printer step — piggybacking on this loop's existing
    round-trip rather than adding a second poll cycle, per the counter/level
    data being similarly slow-changing. Best-effort: a failed toner poll
    (SnmpProbeError) doesn't affect the page-counter poll's own result."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                defaults = await get_or_create_snmp_defaults(db)
                if defaults.enabled:
                    printers = (
                        (
                            await db.execute(
                                select(Printer).where(
                                    Printer.snmp_enabled.is_(True),
                                    Printer.archived_at.is_(None),
                                    Printer.is_virtual.is_(False),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )

                    async def _refresh_one(printer: Printer) -> None:
                        if await refresh_printer_counters(printer, defaults):
                            db.add(record_reading(printer))
                        try:
                            await sync_toner_levels(
                                db, printer, resolve_snmp_config(printer, defaults)
                            )
                        except SnmpProbeError:
                            # Best-effort per printer — one printer's SNMP probe
                            # failing (unreachable, no supply info, etc.) must not
                            # stop the rest of this gather() from completing.
                            pass

                    await asyncio.gather(
                        *(_refresh_one(p) for p in printers), return_exceptions=True
                    )
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in SNMP counter poll loop")
        await asyncio.sleep(SNMP_COUNTER_POLL_INTERVAL_SECONDS)


COUNTER_READING_PURGE_INTERVAL_SECONDS = 24 * 60 * 60


async def _counter_reading_purge_loop() -> None:
    """Deletes PrinterCounterReading and PrinterTonerReading rows older
    than SnmpDefaultsSettings.retention_days (same setting governs both —
    no separate admin config surface for toner history retention) —
    unlike the held-job purge (15 min, time-sensitive since it's deleting
    spooled documents), this isn't urgent: readings accumulate slowly (one
    per printer, or per printer-color for toner, per successful 30-min
    poll) and a day's delay in pruning old ones is harmless, so a 24h
    cadence is plenty. Same tolerant per-cycle error handling as the other
    background loops."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                defaults = await get_or_create_snmp_defaults(db)
                cutoff = datetime.now(UTC) - timedelta(days=defaults.retention_days)
                await db.execute(
                    delete(PrinterCounterReading).where(PrinterCounterReading.recorded_at < cutoff)
                )
                await db.execute(
                    delete(PrinterTonerReading).where(PrinterTonerReading.recorded_at < cutoff)
                )
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in counter reading purge loop")
        await asyncio.sleep(COUNTER_READING_PURGE_INTERVAL_SECONDS)


SYSLOG_EVENT_PURGE_INTERVAL_SECONDS = 24 * 60 * 60


async def _syslog_event_purge_loop() -> None:
    """Deletes PrinterSyslogEvent rows older than
    SyslogSettings.retention_days — same daily cadence and tolerant
    per-cycle error handling as _counter_reading_purge_loop above, and for
    the same reason: not time-sensitive, so a day's delay in pruning is
    harmless."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                settings = await get_or_create_syslog_settings(db)
                cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
                await db.execute(
                    delete(PrinterSyslogEvent).where(PrinterSyslogEvent.received_at < cutoff)
                )
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in syslog event purge loop")
        await asyncio.sleep(SYSLOG_EVENT_PURGE_INTERVAL_SECONDS)


HELD_JOB_PURGE_INTERVAL_SECONDS = 15 * 60


async def _held_job_purge_loop() -> None:
    """Cancels any held job (app/routers/release.py) that was never
    released before Job.held_expires_at — a forgotten sensitive document
    shouldn't sit in the spool indefinitely. Same tolerant per-cycle error
    handling as the other background loops."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(UTC)
                result = await db.execute(
                    select(Job).where(Job.status == "held", Job.held_expires_at < now)
                )
                for job in result.scalars().all():
                    if job.held_file_path:
                        Path(job.held_file_path).unlink(missing_ok=True)
                    job.status = "cancelled"
                    job.error_message = "Expired unreleased hold"
                    job.completed_at = now
                    job.held_file_path = None
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in held job purge loop")
        await asyncio.sleep(HELD_JOB_PURGE_INTERVAL_SECONDS)


FAILED_JOB_PURGE_INTERVAL_SECONDS = 60 * 60
FAILED_JOB_RETENTION = timedelta(hours=48)


async def _failed_job_purge_loop() -> None:
    """Deletes Job rows that have been in a terminal "failed" state for
    more than 48h — keeps the Jobs page from accumulating already-handled
    errors forever. This is a deliberate tradeoff, not an oversight: Print
    Insights (app/reports/aggregation.py) reads its failed_jobs count
    straight from Job rows, no separate aggregate table, so failure counts
    for date ranges older than 48h will undercount once this has run.

    Also unlinks any leftover spooled file — a failed *release* attempt
    (app/routers/release.py) sets status="failed" but, unlike a successful
    release, does not clear held_file_path or delete the file, so without
    this it would otherwise sit in /var/spool/printops-held forever. Same
    tolerant per-cycle error handling as the other background loops."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                cutoff = datetime.now(UTC) - FAILED_JOB_RETENTION
                result = await db.execute(
                    select(Job).where(Job.status == "failed", Job.completed_at < cutoff)
                )
                for job in result.scalars().all():
                    if job.held_file_path:
                        Path(job.held_file_path).unlink(missing_ok=True)
                    await db.delete(job)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in failed job purge loop")
        await asyncio.sleep(FAILED_JOB_PURGE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(
            _make_device_sync_loop("Mosyle", MosyleSettings, run_mosyle_sync, MosyleError)()
        ),
        asyncio.create_task(
            _make_device_sync_loop(
                "Google Workspace",
                GoogleWorkspaceSettings,
                run_google_workspace_sync,
                GoogleWorkspaceError,
            )()
        ),
        asyncio.create_task(_printer_status_poll_loop()),
        asyncio.create_task(_capability_rediscovery_loop()),
        asyncio.create_task(_snmp_counter_poll_loop()),
        asyncio.create_task(_counter_reading_purge_loop()),
        asyncio.create_task(_syslog_event_purge_loop()),
        asyncio.create_task(_held_job_purge_loop()),
        asyncio.create_task(_failed_job_purge_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            # Expected — this loop just requested the cancellation above as
            # part of a normal shutdown, not an error to surface.
            pass


app = FastAPI(
    title=settings.app_name,
    description="PrintOps backend API — enterprise print management platform.",
    version=get_current_version(),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def block_impersonated_mutations(request: Request, call_next):
    """A "View as" session (app/routers/users.py's impersonate_user) is
    meant to be strictly read-only — this is the single, central place
    that's actually guaranteed, rather than something every router has to
    remember to check. Runs ahead of routing entirely: any non-safe
    request carrying a token with an `impersonated_by` claim gets 403'd
    here, regardless of which endpoint it targets or whether that
    endpoint even knows impersonation exists.

    Deliberately fails open on anything that isn't an impersonation token
    (missing/malformed/expired) — that's ordinary auth's job
    (app.deps.get_current_user), not this middleware's.

    Scheme comparison is case-insensitive, matching FastAPI's own
    OAuth2PasswordBearer (fastapi.security.utils.get_authorization_scheme_param
    compares via scheme.lower() == "bearer") — get_current_user accepts
    `Authorization: bearer <token>` just as readily as `Bearer <token>`,
    so this had to too, or a lowercase scheme would sail through this
    check while still authenticating downstream."""
    if request.method not in SAFE_METHODS:
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            token = token.strip()
            try:
                payload = decode_access_token(token, settings)
            except PyJWTError:
                payload = {}
            if payload.get("impersonated_by"):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "This is a read-only \"View as\" session — no changes can be "
                        "made while impersonating another user."
                    },
                )
    return await call_next(request)


app.include_router(health.router)
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(printers.router, prefix="/api/v1/printers", tags=["printers"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(jobs.user_router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(internal.router, prefix="/api/v1/internal", tags=["internal"])
app.include_router(syslog_router.router, prefix="/api/v1/syslog", tags=["syslog"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(device_overrides.router, prefix="/api/v1/devices", tags=["devices"])
app.include_router(
    attribution_aliases.router, prefix="/api/v1/attribution-aliases", tags=["attribution-aliases"]
)
app.include_router(updates.router, prefix="/api/v1/updates", tags=["updates"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])
app.include_router(release.router, prefix="/api/v1/release", tags=["release"])
app.include_router(mfp_devices.router, prefix="/api/v1/mfp-devices", tags=["mfp-devices"])
app.include_router(
    staff_copier_identities.router,
    prefix="/api/v1/staff-copier-identities",
    tags=["staff-copier-identities"],
)
app.include_router(copier_imports.router, prefix="/api/v1/copier-imports", tags=["copier-imports"])
app.include_router(
    copier_unmapped.router, prefix="/api/v1/copier-unmapped", tags=["copier-unmapped"]
)
app.include_router(quota_holds.router, prefix="/api/v1/quota-holds", tags=["quota-holds"])
app.include_router(
    self_service_print.router, prefix="/api/v1/self-service-print", tags=["self-service-print"]
)
app.include_router(
    zabbix_integration.router, prefix="/api/v1/integrations/zabbix", tags=["zabbix"]
)
