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

from app.copiers.account_counters import poll_account_counters
from app.copiers.device_owner import attribute_device_copies
from app.copiers.sync_jobs import create_sync_job, run_sync_job
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
from app.models.mfp_device import MfpDevice
from app.models.mosyle import MosyleSettings
from app.models.printer import Printer
from app.models.report import PrinterTonerReading
from app.models.snmp import PrinterCounterReading
from app.models.syslog import PrinterSyslogEvent
from app.printers import offline_holds
from app.printers.discovery import refresh_printer_capabilities
from app.printers.job_reconcile import reconcile_stuck_jobs
from app.printers.snmp_counters import (
    SnmpProbeError,
    get_or_create_snmp_defaults,
    record_reading,
    refresh_printer_counters,
    resolve_snmp_config,
    sync_toner_levels,
)
from app.printers.status import refresh_printer_status_and_rediscover, run_automatic_queue_sync
from app.routers import (
    attribution_aliases,
    auth,
    copier_imports,
    copier_unmapped,
    device_overrides,
    health,
    held_jobs,
    internal,
    jobs,
    mfp_devices,
    print_queue,
    printers,
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

                # Sequentially, and on this loop's own session: the refreshes
                # above run concurrently, and SQLAlchemy will not have several
                # of them issuing queries on one session at once. gather() is
                # holding return_exceptions=True, so such a failure would be
                # swallowed while the printer was still committed as online —
                # and the jobs that release missed would never be looked at
                # again.
                #
                # Every online printer, not only the ones that just came back:
                # the backend registers a job and only PATCHes it to "held" a
                # moment later, so a job arriving in that gap would be missed
                # by a transition-triggered release and stranded, the printer
                # having already been recorded as online. One indexed query per
                # online printer per cycle is worth not depending on catching
                # an instant.
                for printer in printers:
                    try:
                        if printer.status == "online":
                            await offline_holds.release_jobs_waiting_for(db, printer)
                            continue
                        # Offline with work behind it. Most of the time that is
                        # a printer switched off for the night and nothing worth
                        # saying; when it is not, this is the only place anybody
                        # would find out.
                        alert = await offline_holds.waiting_alert(db, printer)
                        if alert:
                            printer.status_message = alert
                            printer.status_reasons = [
                                *(printer.status_reasons or []),
                                offline_holds.UNREACHABLE_REASON,
                            ]
                            logger.warning("%s: %s", printer.name, alert)
                    except Exception:
                        logger.exception("Could not check jobs waiting for %s", printer.name)
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
                        # Rate-limited and gated on cupsd having slots to
                        # spare, same as the status loop's resync — a
                        # printer whose reported media flaps would
                        # otherwise resync every cycle forever.
                        await run_automatic_queue_sync(printer)

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


STUCK_JOB_RECONCILE_INTERVAL_SECONDS = 10 * 60


async def _stuck_job_reconcile_loop() -> None:
    """Resolves job records left saying "forwarding" by a CUPS backend that
    died without reporting an outcome — see app/printers/job_reconcile.py for
    how one gets that way and why cupsd's own job record is the only witness.

    Ten minutes rather than the 60-second status cadence: nothing here is
    urgent (the printing already happened or already didn't), each row costs
    an ipptool round trip, and the module will not touch a row until it is
    half an hour old anyway. Same tolerant per-cycle error handling as the
    other background loops."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await reconcile_stuck_jobs(db)
        except Exception:
            logger.exception("Unexpected error in stuck job reconcile loop")
        await asyncio.sleep(STUCK_JOB_RECONCILE_INTERVAL_SECONDS)


COPIER_USER_SYNC_INTERVAL_SECONDS = 6 * 60 * 60


async def _copier_user_sync_loop() -> None:
    """Pushes staff accounts to copiers that have auto_sync_users on.

    Six-hourly rather than minutes: the input is the Google Workspace
    roster, which itself syncs on a slow cycle, and each run takes the
    device's single admin session — polling hard would fight with an admin
    trying to use the copier's own web UI.

    Devices are handled one at a time, not gathered: the whole point of the
    per-device lock in app/copiers/konica_admin.py is that a bizhub allows
    one admin session, and hitting several copiers at once is fine but
    hitting one twice is not. Sequential keeps the failure modes simple and
    the load trivial at this fleet size.

    Every outcome is recorded on the device (last_user_sync_*) so an admin
    can see what happened without reading logs — including the honest
    failures: Account Track switched off, a person already logged into the
    copier, or no admin password stored."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                devices = (
                    (await db.execute(select(MfpDevice).where(MfpDevice.auto_sync_users.is_(True))))
                    .scalars()
                    .all()
                )
                for device in devices:
                    # Goes through the job runner, not the connector
                    # directly: that is what consults the provisioning
                    # record and skips people already on the device. Calling
                    # the connector here would re-add everyone and the
                    # device would reject every one as a duplicate password
                    # — which it did, every cycle, until this was fixed.
                    job = await create_sync_job(db, device, trigger="schedule")
                    if job is None:
                        continue  # a run is already in flight for this device
                    await run_sync_job(job.id)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in copier user sync loop")
        await asyncio.sleep(COPIER_USER_SYNC_INTERVAL_SECONDS)


COPIER_COUNTER_POLL_INTERVAL_SECONDS = 60 * 60


async def _copier_counter_poll_loop() -> None:
    """Reads per-account counters from copiers that have
    auto_poll_counters on, and records the usage since the last read.

    Hourly, where the user sync is six-hourly: this one only reads, it is
    a handful of requests rather than one per person, and the interval is
    what bounds how precisely a copy can be dated. The counters carry no
    timestamps at all — all PrintOps can say is "between these two reads"
    — so the poll interval *is* the resolution of the resulting usage
    data. An hour is a good deal more useful than six for that.

    Sequentially and for the same reason as the sync loop: one admin
    session per device.

    A device that has never been polled produces no usage on its first
    run, only a baseline to subtract from next time. That is expected, and
    the message recorded on the device says as much rather than implying
    nobody used the copier."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                devices = (
                    (
                        await db.execute(
                            select(MfpDevice).where(MfpDevice.auto_poll_counters.is_(True))
                        )
                    )
                    .scalars()
                    .all()
                )
                for device in devices:
                    try:
                        result = await poll_account_counters(db, device)
                        logger.info("Counter poll for %s: %s", device.name, result.message)
                    except Exception as exc:  # noqa: BLE001
                        # One unreachable copier must not stop the rest of
                        # the fleet being read, and the reason belongs on
                        # the device where an admin will look for it.
                        logger.warning("Counter poll failed for %s: %s", device.name, exc)
                        device.last_counter_poll_at = datetime.now(UTC)
                        device.last_counter_poll_ok = False
                        device.last_counter_poll_message = str(exc)
                        await db.commit()

                # Copiers nobody logs in to, whose whole meter belongs to
                # one named person (app/copiers/device_owner.py). A
                # separate pass over a different set of devices — these
                # typically have auto_poll_counters off, because there are
                # no per-account counters on them to poll. It runs here
                # rather than in its own loop because it costs nothing to
                # run: no device is contacted at all, it only diffs SNMP
                # readings the counter loop already recorded.
                owned = (
                    (
                        await db.execute(
                            select(MfpDevice).where(MfpDevice.default_owner_email.is_not(None))
                        )
                    )
                    .scalars()
                    .all()
                )
                for device in owned:
                    try:
                        result = await attribute_device_copies(db, device)
                        if result.usage_rows or result.baselined:
                            logger.info(
                                "Default-owner attribution for %s: %s", device.name, result.message
                            )
                    except Exception:
                        logger.exception("Default-owner attribution failed for %s", device.name)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in copier counter poll loop")
        await asyncio.sleep(COPIER_COUNTER_POLL_INTERVAL_SECONDS)


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
        asyncio.create_task(_stuck_job_reconcile_loop()),
        asyncio.create_task(_copier_user_sync_loop()),
        asyncio.create_task(_copier_counter_poll_loop()),
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
                        "detail": 'This is a read-only "View as" session — no changes can be '
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
app.include_router(held_jobs.router, prefix="/api/v1/held-jobs", tags=["held-jobs"])
# Any signed-in member of staff, not just admins: this is the one page that
# answers "where is my job in the line" (app/routers/print_queue.py).
app.include_router(print_queue.router, prefix="/api/v1/print-queue", tags=["print-queue"])
app.include_router(
    self_service_print.router, prefix="/api/v1/self-service-print", tags=["self-service-print"]
)
app.include_router(zabbix_integration.router, prefix="/api/v1/integrations/zabbix", tags=["zabbix"])
