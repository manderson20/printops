import asyncio
import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt, encrypt
from app.core.security import hash_password
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.models.printer import Printer
from app.models.printer_ou_access import PrinterAllowedOu
from app.models.quota import PrinterUserQuota
from app.models.release_bypass import PrinterReleaseBypass
from app.models.report import PrinterTonerCartridge
from app.printers.counter_history import get_daily_deltas
from app.printers.cups_ppd_info import get_cups_queue_default_page_size
from app.printers.discovery import refresh_printer_capabilities
from app.printers.job_control import JobControlError, purge_cups_queue
from app.printers.queue_sync import QueueSyncError, remove_queue, sync_queue
from app.printers.snmp_counters import (
    SnmpProbeError,
    get_or_create_snmp_defaults,
    record_reading,
    refresh_printer_counters,
    resolve_snmp_config,
    sync_toner_levels,
)
from app.printers.status import refresh_printer_status_and_rediscover
from app.printers.test_print import TestPrintError, submit_test_print
from app.printers.toner_history import get_daily_toner_levels
from app.quotas.service import get_pages_used, period_bounds
from app.schemas.auth import UserOut
from app.schemas.printer import (
    CupsQueueDefaultsOut,
    PrinterCreate,
    PrinterMdmConnectionOut,
    PrinterOut,
    PrinterUpdate,
    VirtualQueueCreate,
)
from app.schemas.printer_ou_access import PrinterAllowedOuCreate, PrinterAllowedOuOut
from app.schemas.quota import PrinterUserQuotaCreate, PrinterUserQuotaOut, PrinterUserQuotaUpdate
from app.schemas.release_bypass import PrinterReleaseBypassCreate, PrinterReleaseBypassOut
from app.schemas.report import (
    BulkCartridgeUpdateIn,
    CartridgeIn,
    CartridgeOut,
    DailyTonerLevelOut,
    DetectCartridgesResult,
    DetectedSupplyOut,
    FleetCartridgeOut,
)
from app.schemas.snmp import DailyCounterDeltaOut
from app.schemas.syslog import SyslogEventPage
from app.server_settings.service import get_or_create_server_settings
from app.syslog.service import list_events as list_syslog_events

router = APIRouter(dependencies=[Depends(get_current_user)])

# Fields that affect the CUPS queue (device-uri, PPD, sharing, AirPrint
# advertisement) — an update touching only these should trigger a re-sync.
# Anything else (notes, department, building...) shouldn't cause CUPS/Avahi
# churn.
QUEUE_AFFECTING_FIELDS = {
    "name",
    "ip_address",
    "port",
    "use_tls",
    "ipp_path",
    "airprint_enabled",
    "release_required",
}


async def _get_printer_or_404(printer_id: UUID, db: AsyncSession) -> Printer:
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")
    return printer


async def _apply_queue_sync(printer: Printer, db: AsyncSession) -> None:
    """Creates/updates the printer's CUPS queue to match its current
    connection details. Must run after the printer is already committed —
    the sync script reads connection info back via the internal API, which
    reads the DB. Non-fatal: failure is recorded on the printer, not raised,
    so a print-server hiccup doesn't block adding/editing a printer.

    This can run long (sync_cups_queue.sh's bounded-timeout + generic-PPD
    fallback for devices that can't handle -m everywhere's full attribute
    probe — confirmed live against a real Kyocera, over 90s worst case
    across both the client and release queue scripts) — long enough for
    the printer to be legitimately deleted by an admin while this is still
    in flight. Treated as a benign no-op rather than a crash: nothing left
    to record the sync result on."""
    try:
        await asyncio.to_thread(sync_queue, str(printer.id), printer.is_virtual)
        printer.queue_sync_error = None
    except QueueSyncError as exc:
        printer.queue_sync_error = str(exc)
    try:
        await db.commit()
    except StaleDataError:
        await db.rollback()
        return
    await db.refresh(printer)


@router.post(
    "",
    response_model=PrinterOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_printer(payload: PrinterCreate, db: AsyncSession = Depends(get_db)):
    printer = Printer(
        name=payload.name,
        ip_address=str(payload.ip_address),
        port=payload.port,
        use_tls=payload.use_tls,
        ipp_path=payload.ipp_path,
        airprint_enabled=payload.airprint_enabled,
        manufacturer=payload.manufacturer,
        model=payload.model,
        hostname=payload.hostname,
        serial_number=payload.serial_number,
        building=payload.building,
        room=payload.room,
        department=payload.department,
        notes=payload.notes,
        snmp_enabled=payload.snmp_enabled,
        snmp_port=payload.snmp_port,
        snmp_version=payload.snmp_version,
        snmp_vendor_profile=payload.snmp_vendor_profile,
        snmp_community_encrypted=(
            encrypt(payload.snmp_community) if payload.snmp_community else None
        ),
    )
    await refresh_printer_capabilities(printer)
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    await _apply_queue_sync(printer, db)
    return printer


@router.post(
    "/virtual",
    response_model=PrinterOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_virtual_queue(payload: VirtualQueueCreate, db: AsyncSession = Depends(get_db)):
    """A Follow-Me queue with no real device behind it — see
    VirtualQueueCreate's docstring. follow_me_enabled/airprint_enabled are
    forced on here rather than left to the payload: a virtual queue that
    isn't discoverable or releasable elsewhere would be pointless, and
    update_printer refuses to ever turn follow_me_enabled back off for one.
    No refresh_printer_capabilities call (unlike create_printer) — there's
    no real device to probe."""
    printer = Printer(
        name=payload.name,
        is_virtual=True,
        ip_address=None,
        airprint_enabled=True,
        follow_me_enabled=True,
        release_token=secrets.token_urlsafe(16),
        snmp_enabled=False,
        building=payload.building,
        room=payload.room,
        department=payload.department,
        notes=payload.notes,
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    await _apply_queue_sync(printer, db)
    return printer


@router.get("", response_model=list[PrinterOut])
async def list_printers(include_archived: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(Printer).order_by(Printer.name)
    if not include_archived:
        stmt = stmt.where(Printer.archived_at.is_(None))
    result = await db.execute(stmt)
    return result.scalars().all()


_FLEET_CARTRIDGE_COLUMNS = (
    PrinterTonerCartridge,
    Printer.name,
    Printer.manufacturer,
    Printer.model,
    Printer.building,
    Printer.room,
)


def _fleet_cartridge_out(row: tuple) -> FleetCartridgeOut:
    cartridge, printer_name, manufacturer, printer_model, building, room = row
    return FleetCartridgeOut(
        id=cartridge.id,
        printer_id=cartridge.printer_id,
        printer_name=printer_name,
        printer_manufacturer=manufacturer,
        printer_model=printer_model,
        building=building,
        room=room,
        color=cartridge.color,
        cost=cartridge.cost,
        yield_pages=cartridge.yield_pages,
        model=cartridge.model,
        warning_threshold_percent=cartridge.warning_threshold_percent,
        current_level_percent=cartridge.current_level_percent,
    )


@router.get("/toner-cartridges", response_model=list[FleetCartridgeOut])
async def list_fleet_toner_cartridges(db: AsyncSession = Depends(get_db)):
    """Every non-archived printer's cartridges in one flat list, with
    enough printer context to display and group them fleet-wide — powers
    Settings > Toner Cartridges (the bulk cost/yield editor), grouping
    rows client-side by printer. Read-only telemetry, open to any
    logged-in user, same convention as the per-printer GET further below.

    Registered here, before GET /{printer_id} below, on purpose — both
    are single-segment GETs under this router's /printers prefix, and
    Starlette matches by registration order, not specificity; if
    /{printer_id} came first, a request for /printers/toner-cartridges
    would match it instead (attempting, and failing, to parse
    "toner-cartridges" as a UUID) and this route would never be reached."""
    result = await db.execute(
        select(*_FLEET_CARTRIDGE_COLUMNS)
        .join(Printer, Printer.id == PrinterTonerCartridge.printer_id)
        .where(Printer.archived_at.is_(None))
        .order_by(Printer.name, PrinterTonerCartridge.color)
    )
    return [_fleet_cartridge_out(row) for row in result.all()]


@router.patch(
    "/toner-cartridges/bulk",
    response_model=list[FleetCartridgeOut],
    dependencies=[Depends(require_role("admin"))],
)
async def bulk_update_toner_cartridges(
    payload: list[BulkCartridgeUpdateIn], db: AsyncSession = Depends(get_db)
):
    """Updates cost/yield_pages/model on existing PrinterTonerCartridge
    rows by id, across as many printers as the caller likes in one
    request — the mass-edit counterpart to the per-printer PUT further
    below, which replaces one printer's whole set and isn't a fit here
    (these rows already exist; color/detected_*/warning_threshold_percent
    aren't touched, only the fields this bulk-edit page actually exposes).
    Same single row, same PrinterTonerCartridge.model column the
    per-printer Toner tab reads/writes — editing it here is immediately
    reflected there, not a separate copy. Silently skips an id that no
    longer exists (e.g. the cartridge or its printer was deleted between
    page load and save) rather than failing the whole batch over one
    stale row."""
    ids = [entry.id for entry in payload]
    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.id.in_(ids))
    )
    rows_by_id = {row.id: row for row in result.scalars().all()}

    for entry in payload:
        row = rows_by_id.get(entry.id)
        if row is None:
            continue
        row.cost = entry.cost
        row.yield_pages = entry.yield_pages
        row.model = entry.model
    await db.commit()

    result = await db.execute(
        select(*_FLEET_CARTRIDGE_COLUMNS)
        .join(Printer, Printer.id == PrinterTonerCartridge.printer_id)
        .where(PrinterTonerCartridge.id.in_(ids))
    )
    return [_fleet_cartridge_out(row) for row in result.all()]


@router.get("/{printer_id}", response_model=PrinterOut)
async def get_printer(
    printer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    printer = await _get_printer_or_404(printer_id, db)
    out = PrinterOut.model_validate(printer)
    # Reference-only credentials (app/models/printer.py) -- the only place
    # the real decrypted values are ever attached, and only for an admin;
    # everywhere else (including list_printers) they stay at their safe
    # None default.
    if current_user.role == "admin":
        if printer.web_login_password_encrypted:
            out.web_login_password = decrypt(printer.web_login_password_encrypted)
        if printer.scan_password_encrypted:
            out.scan_password = decrypt(printer.scan_password_encrypted)
    return out


@router.patch(
    "/{printer_id}", response_model=PrinterOut, dependencies=[Depends(require_role("admin"))]
)
async def update_printer(
    printer_id: UUID, payload: PrinterUpdate, db: AsyncSession = Depends(get_db)
):
    printer = await _get_printer_or_404(printer_id, db)
    updates = payload.model_dump(exclude_unset=True)
    # A virtual queue (app/models/printer.py:is_virtual) has no physical
    # location of its own to release at — it must always be held and only
    # ever released elsewhere, so neither half of that invariant can be
    # relaxed after creation.
    if printer.is_virtual:
        if updates.get("follow_me_enabled") is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Follow-Me can't be turned off for a virtual queue.",
            )
        if updates.get("release_required") is True:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A virtual queue has no physical location to require release at.",
            )
    if "ip_address" in updates and updates["ip_address"] is not None:
        updates["ip_address"] = str(updates["ip_address"])
    # A blank string clears one of these overrides back to "use the global
    # SnmpDefaultsSettings" — same idiom already established for
    # GoogleWorkspaceSettings.staff_org_unit_path in app/routers/settings.py.
    # ldap_bind_username gets the same treatment so clearing it to "" can't
    # collide with another printer's blank value under its unique constraint.
    for field in ("snmp_version", "snmp_port", "snmp_vendor_profile", "ldap_bind_username"):
        if field in updates:
            updates[field] = updates[field] or None
    # Community/bind-password are secrets that can't go through the generic
    # setattr loop below like the other overrides — only overwrite when a
    # non-empty value is supplied (mirrors update_mosyle_settings' secret
    # handling); a blank string clears it back to "not set" too.
    _not_provided = object()
    snmp_community = updates.pop("snmp_community", _not_provided)
    ldap_bind_password = updates.pop("ldap_bind_password", _not_provided)
    # web_login_password/scan_password are read-only properties on Printer
    # (see its docstring) — must be popped before the generic setattr loop
    # below, same as the other two secrets, or setattr raises on them.
    web_login_password = updates.pop("web_login_password", _not_provided)
    scan_password = updates.pop("scan_password", _not_provided)
    for field, value in updates.items():
        setattr(printer, field, value)
    if snmp_community is not _not_provided:
        printer.snmp_community_encrypted = encrypt(snmp_community) if snmp_community else None
    if web_login_password is not _not_provided:
        printer.web_login_password_encrypted = (
            encrypt(web_login_password) if web_login_password else None
        )
    if scan_password is not _not_provided:
        printer.scan_password_encrypted = encrypt(scan_password) if scan_password else None
    if ldap_bind_password is not _not_provided:
        printer.ldap_bind_password_hash = (
            hash_password(ldap_bind_password) if ldap_bind_password else None
        )
    # First time release (either mode) is turned on for this printer, it
    # needs a token to exist at all — generated here rather than requiring a
    # separate manual step before the toggle does anything useful. Both
    # modes share one kiosk/token. Regenerating an existing one (e.g. a
    # lost/reissued kiosk) is POST /{id}/regenerate-release-token.
    if (printer.release_required or printer.follow_me_enabled) and not printer.release_token:
        printer.release_token = secrets.token_urlsafe(16)
    await db.commit()
    await db.refresh(printer)
    if QUEUE_AFFECTING_FIELDS & updates.keys():
        await _apply_queue_sync(printer, db)
    return printer


@router.delete(
    "/{printer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    printer = await _get_printer_or_404(printer_id, db)
    try:
        await asyncio.to_thread(remove_queue, str(printer.id))
    except QueueSyncError:
        pass  # best-effort — don't block a delete the admin explicitly asked for
    await db.delete(printer)
    await db.commit()


@router.post(
    "/{printer_id}/archive",
    response_model=PrinterOut,
    dependencies=[Depends(require_role("admin"))],
)
async def archive_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Retires this printer without losing its history — see
    Printer.archived_at's docstring. Tears down the CUPS queue (same
    remove_queue() delete_printer uses) so it stops accepting jobs, but
    the row and every Job row pointing at it stay put — unlike delete,
    which cascade-deletes Job history along with the row."""
    printer = await _get_printer_or_404(printer_id, db)
    try:
        await asyncio.to_thread(remove_queue, str(printer.id))
    except QueueSyncError:
        pass  # best-effort — don't block an archive the admin explicitly asked for
    printer.archived_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(printer)
    return printer


@router.post(
    "/{printer_id}/unarchive",
    response_model=PrinterOut,
    dependencies=[Depends(require_role("admin"))],
)
async def unarchive_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Reverses archive_printer — re-syncs the CUPS queue so the printer
    accepts jobs again."""
    printer = await _get_printer_or_404(printer_id, db)
    printer.archived_at = None
    await db.commit()
    await db.refresh(printer)
    await _apply_queue_sync(printer, db)
    return printer


@router.post(
    "/{printer_id}/discover",
    response_model=PrinterOut,
    dependencies=[Depends(require_role("admin"))],
)
async def discover_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    printer = await _get_printer_or_404(printer_id, db)
    if printer.is_virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A virtual queue has no real device to discover capabilities from.",
        )
    await refresh_printer_capabilities(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


@router.post(
    "/{printer_id}/resync-queue",
    response_model=PrinterOut,
    dependencies=[Depends(require_role("admin"))],
)
async def resync_queue(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Manually retries the CUPS queue sync — e.g. after fixing whatever
    caused queue_sync_error, without needing another printer edit."""
    printer = await _get_printer_or_404(printer_id, db)
    await _apply_queue_sync(printer, db)
    return printer


@router.post(
    "/{printer_id}/regenerate-release-token",
    response_model=PrinterOut,
    dependencies=[Depends(require_role("admin"))],
)
async def regenerate_release_token(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Rotates this printer's kiosk release URL (app/routers/release.py) —
    e.g. a lost/reissued kiosk iPad. The old URL stops working immediately
    since it's looked up by token on every call, not cached anywhere."""
    printer = await _get_printer_or_404(printer_id, db)
    printer.release_token = secrets.token_urlsafe(16)
    await db.commit()
    await db.refresh(printer)
    return printer


@router.post("/{printer_id}/check-status", response_model=PrinterOut)
async def check_status(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Manually refreshes a printer's online/error/offline status on demand
    — same underlying probe (and same offline->online rediscovery trigger,
    which as of the queue-sync fix below can also re-run the CUPS queue
    sync) as the 60s background loop (app/main.py), just triggered
    immediately instead of waiting for the next cycle. Open to any
    logged-in user (not admin-gated) like GET, same as before."""
    printer = await _get_printer_or_404(printer_id, db)
    if printer.is_virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A virtual queue has no real device to check the status of.",
        )
    await refresh_printer_status_and_rediscover(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


@router.post("/{printer_id}/check-counters", response_model=PrinterOut)
async def check_counters(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Manually refreshes a printer's SNMP page/copy/print counters on
    demand — same underlying probe as the 30-min background loop
    (app/main.py), just triggered immediately. Read-only telemetry, so
    open to any logged-in user (not admin-gated), matching check-status."""
    printer = await _get_printer_or_404(printer_id, db)
    if printer.is_virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A virtual queue has no real device to poll SNMP counters from.",
        )
    defaults = await get_or_create_snmp_defaults(db)
    if await refresh_printer_counters(printer, defaults):
        db.add(record_reading(printer))
    await db.commit()
    await db.refresh(printer)
    return printer


@router.get("/{printer_id}/counter-history", response_model=list[DailyCounterDeltaOut])
async def counter_history(printer_id: UUID, days: int = 30, db: AsyncSession = Depends(get_db)):
    """Per-day usage deltas computed from the SNMP counter reading
    history (app/printers/counter_history.py) — powers the printer
    detail page's usage-over-time chart. Read-only telemetry, open to
    any logged-in user, matching check-counters/check-status."""
    await _get_printer_or_404(printer_id, db)
    return await get_daily_deltas(db, printer_id, days)


@router.get("/{printer_id}/toner-history", response_model=list[DailyTonerLevelOut])
async def toner_history(printer_id: UUID, days: int = 30, db: AsyncSession = Depends(get_db)):
    """Per-day toner level history computed from PrinterTonerReading
    (app/printers/toner_history.py) — powers the printer detail page's
    toner-level-over-time chart. Read-only telemetry, open to any logged-in
    user, matching counter-history above."""
    await _get_printer_or_404(printer_id, db)
    return await get_daily_toner_levels(db, printer_id, days)


@router.get("/{printer_id}/syslog", response_model=SyslogEventPage)
async def printer_syslog_events(
    printer_id: UUID,
    severity: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Syslog events received from this printer (infra/syslog-relay/),
    matched by source IP — see app/models/syslog.py. Read-only telemetry,
    open to any logged-in user, matching counter-history/check-status."""
    await _get_printer_or_404(printer_id, db)
    items, total = await list_syslog_events(
        db,
        printer_id=printer_id,
        severity=severity,
        search=search,
        page=page,
        page_size=min(page_size, 200),
    )
    return SyslogEventPage(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/{printer_id}/purge-jobs",
    dependencies=[Depends(require_role("admin"))],
)
async def purge_jobs(
    printer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Cancels this printer's entire CUPS queue — not just the jobs PrintOps
    can see, but anything backed up behind them too, since a job only gets a
    Job row once CUPS actually starts running our backend for it (see
    infra/cups/backends/printops). For when a jam/error has backed up
    several jobs and an admin just wants the queue cleared, not to hunt down
    each one individually."""
    printer = await _get_printer_or_404(printer_id, db)
    try:
        await asyncio.to_thread(purge_cups_queue, str(printer.id))
    except JobControlError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    cancel_note = f"Cancelled via queue purge by {current_user.username}"
    result = await db.execute(
        update(Job)
        .where(Job.printer_id == printer_id, Job.status == "forwarding")
        .values(status="cancelled", error_message=cancel_note, completed_at=datetime.now(UTC))
    )
    await db.commit()
    return {"cancelled_count": result.rowcount}


@router.get("/{printer_id}/mdm-connection", response_model=PrinterMdmConnectionOut)
async def get_mdm_connection(
    printer_id: UUID,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Connection details for manually adding this printer's PrintOps queue
    in an MDM tool (Mosyle etc) — points at the CUPS server, not the real
    printer, since clients print through PrintOps. Hostname comes from the
    admin-editable ServerSettings row (Settings > Server), not the env-only
    Settings.print_server_host default it's seeded from — confirmed live
    this was still reading the static env value even after the hostname
    was changed on the settings page, so newly-configured MDM profiles
    kept pointing admins at the raw IP instead of the real domain."""
    printer = await _get_printer_or_404(printer_id, db)
    server_settings = await get_or_create_server_settings(db)
    queue_name = f"printops-{printer.id}"
    resource_path = f"/printers/{queue_name}"
    return PrinterMdmConnectionOut(
        queue_name=queue_name,
        host=server_settings.hostname,
        port=settings.print_server_port,
        resource_path=resource_path,
        ipp_uri=f"ipp://{server_settings.hostname}:{settings.print_server_port}{resource_path}",
        airprint_enabled=printer.airprint_enabled,
    )


@router.get(
    "/{printer_id}/cups-queue-defaults",
    response_model=CupsQueueDefaultsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def get_cups_queue_defaults(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """On-demand (not stored) read of the CUPS queue's own current PPD
    PageSize default, so it can be shown next to the device's IPP-reported
    default_media_size — a mismatch is the same diagnostic signal that
    identified the earlier *DefaultColorModel bug (see
    scripts/sync_cups_queue.sh's ColorModel=RGB patch). Kept out of
    GET /printers/{id} so that main fetch stays DB-only; this one shells
    out, so the frontend calls it lazily."""
    printer = await _get_printer_or_404(printer_id, db)
    page_size = await asyncio.to_thread(get_cups_queue_default_page_size, str(printer.id))
    return CupsQueueDefaultsOut(page_size=page_size)


@router.post("/{printer_id}/test-print", dependencies=[Depends(require_role("admin"))])
async def test_print(
    printer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    printer = await _get_printer_or_404(printer_id, db)
    try:
        message = await asyncio.to_thread(
            submit_test_print, str(printer.id), printer.name, current_user.username
        )
    except TestPrintError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"message": message}


@router.get("/{printer_id}/toner-cartridges", response_model=list[CartridgeOut])
async def get_toner_cartridges(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_printer_or_404(printer_id, db)
    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    return result.scalars().all()


@router.put(
    "/{printer_id}/toner-cartridges",
    response_model=list[CartridgeOut],
    dependencies=[Depends(require_role("admin"))],
)
async def update_toner_cartridges(
    printer_id: UUID, payload: list[CartridgeIn], db: AsyncSession = Depends(get_db)
):
    """Replaces this printer's full cartridge set — simplest correct
    approach for a handful (<=4) of rows representing "current cost
    profile per color slot", not a purchase ledger (see
    PrinterTonerCartridge's docstring). Used by app/reports/ cost
    calculations (app/routers/reports.py's cost-breakdown endpoint)."""
    await _get_printer_or_404(printer_id, db)
    colors = [entry.color for entry in payload]
    if len(colors) != len(set(colors)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each cartridge color can only be listed once.",
        )

    # Carry over each color's detected_*/level fields (app/printers/
    # snmp_counters.py: sync_toner_levels, via the /detect endpoint below
    # and the 30-minute background poll in app/main.py) across this
    # delete-and-recreate — an admin correcting the cost/yield_pages an
    # SNMP poll just surfaced shouldn't wipe that same poll's result.
    existing = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    detected_by_color = {
        row.color: (
            row.detected_description,
            row.detected_high_capacity,
            row.detected_at,
            row.current_level_percent,
            row.level_checked_at,
        )
        for row in existing.scalars().all()
    }

    await db.execute(
        PrinterTonerCartridge.__table__.delete().where(
            PrinterTonerCartridge.printer_id == printer_id
        )
    )
    for entry in payload:
        (
            detected_description,
            detected_high_capacity,
            detected_at,
            current_level_percent,
            level_checked_at,
        ) = detected_by_color.get(entry.color, (None, None, None, None, None))
        db.add(
            PrinterTonerCartridge(
                printer_id=printer_id,
                color=entry.color,
                cost=entry.cost,
                yield_pages=entry.yield_pages,
                model=entry.model,
                warning_threshold_percent=entry.warning_threshold_percent,
                detected_description=detected_description,
                detected_high_capacity=detected_high_capacity,
                detected_at=detected_at,
                current_level_percent=current_level_percent,
                level_checked_at=level_checked_at,
            )
        )
    await db.commit()

    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    return result.scalars().all()


@router.post(
    "/{printer_id}/toner-cartridges/detect",
    response_model=DetectCartridgesResult,
    dependencies=[Depends(require_role("admin"))],
)
async def detect_toner_cartridges(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Probes the printer's cartridge supplies over SNMP (see
    app/printers/snmp_counters.py:get_toner_supplies for why color/
    high-capacity are always a best-effort guess, never asserted as
    confirmed) and upserts detected_*/current_level_percent onto each
    matched color's row via sync_toner_levels — cost/yield_pages are left
    untouched (SNMP has no concept of a dollar cost), creating a new row
    with cost=0/yield_pages=0 for any detected color that doesn't have one
    yet. compute_printer_rate already treats yield_pages=0 as "not
    configured" and falls back to the flat rate, so a freshly-detected,
    not-yet-priced row is safe, not a silent zero cost. Supplies the probe
    saw but couldn't confidently color-match are returned in `unmatched`
    rather than dropped. sync_toner_levels is the same function the
    30-minute background poll (app/main.py) uses, so a manual detect and
    the automatic one behave identically."""
    printer = await _get_printer_or_404(printer_id, db)
    defaults = await get_or_create_snmp_defaults(db)
    config = resolve_snmp_config(printer, defaults)

    try:
        unmatched_supplies = await sync_toner_levels(db, printer, config)
    except SnmpProbeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    await db.commit()

    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    unmatched = [DetectedSupplyOut(**vars(supply)) for supply in unmatched_supplies]
    return DetectCartridgesResult(cartridges=result.scalars().all(), unmatched=unmatched)


async def _quota_out(db: AsyncSession, quota: PrinterUserQuota) -> PrinterUserQuotaOut:
    start, end = period_bounds(quota.period, datetime.now(UTC))
    pages_used = (
        await get_pages_used(db, quota.printer_id, quota.user_email, start, end)
        if quota.user_email
        else 0
    )
    return PrinterUserQuotaOut(
        id=quota.id,
        printer_id=quota.printer_id,
        user_email=quota.user_email,
        period=quota.period,
        page_limit=quota.page_limit,
        pages_used=pages_used,
    )


@router.get("/{printer_id}/quotas", response_model=list[PrinterUserQuotaOut])
async def list_printer_quotas(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_printer_or_404(printer_id, db)
    result = await db.execute(
        select(PrinterUserQuota)
        .where(PrinterUserQuota.printer_id == printer_id)
        .order_by(PrinterUserQuota.user_email.is_(None).desc(), PrinterUserQuota.user_email)
    )
    return [await _quota_out(db, quota) for quota in result.scalars().all()]


@router.post(
    "/{printer_id}/quotas",
    response_model=PrinterUserQuotaOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_printer_quota(
    printer_id: UUID, payload: PrinterUserQuotaCreate, db: AsyncSession = Depends(get_db)
):
    """A quota's user_email is a specific staff member, or None for a
    per-printer default/wildcard row (see PrinterUserQuota's docstring) —
    at most one of each per printer, enforced below (specific rows) and by
    a partial unique index (default rows, see app/models/quota.py)."""
    await _get_printer_or_404(printer_id, db)

    email = payload.user_email.strip().lower() if payload.user_email else None
    if email is not None:
        roster_match = await db.execute(
            select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email)
        )
        if roster_match.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"'{payload.user_email}' is not in the synced Google Workspace user "
                    "roster — sync Google Workspace settings, or double-check the address."
                ),
            )

    existing = await db.execute(
        select(PrinterUserQuota).where(
            PrinterUserQuota.printer_id == printer_id,
            PrinterUserQuota.user_email == email
            if email is not None
            else PrinterUserQuota.user_email.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        detail = (
            f"A quota for '{payload.user_email}' already exists on this printer."
            if email is not None
            else "A default quota already exists on this printer."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    quota = PrinterUserQuota(
        printer_id=printer_id,
        user_email=email,
        period=payload.period,
        page_limit=payload.page_limit,
    )
    db.add(quota)
    await db.commit()
    await db.refresh(quota)
    return await _quota_out(db, quota)


@router.patch(
    "/{printer_id}/quotas/{quota_id}",
    response_model=PrinterUserQuotaOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_printer_quota(
    printer_id: UUID,
    quota_id: UUID,
    payload: PrinterUserQuotaUpdate,
    db: AsyncSession = Depends(get_db),
):
    quota = await db.get(PrinterUserQuota, quota_id)
    if quota is None or quota.printer_id != printer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quota not found")
    if payload.period is not None:
        quota.period = payload.period
    if payload.page_limit is not None:
        quota.page_limit = payload.page_limit
    await db.commit()
    await db.refresh(quota)
    return await _quota_out(db, quota)


@router.delete(
    "/{printer_id}/quotas/{quota_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_printer_quota(
    printer_id: UUID, quota_id: UUID, db: AsyncSession = Depends(get_db)
):
    quota = await db.get(PrinterUserQuota, quota_id)
    if quota is None or quota.printer_id != printer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quota not found")
    await db.delete(quota)
    await db.commit()


@router.get("/{printer_id}/release-bypasses", response_model=list[PrinterReleaseBypassOut])
async def list_printer_release_bypasses(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_printer_or_404(printer_id, db)
    result = await db.execute(
        select(PrinterReleaseBypass)
        .where(PrinterReleaseBypass.printer_id == printer_id)
        .order_by(PrinterReleaseBypass.user_email)
    )
    return result.scalars().all()


@router.post(
    "/{printer_id}/release-bypasses",
    response_model=PrinterReleaseBypassOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_printer_release_bypass(
    printer_id: UUID, payload: PrinterReleaseBypassCreate, db: AsyncSession = Depends(get_db)
):
    """Lets a specific staff member skip the PIN-release hold at this one
    printer, even while release_required is on — see
    app/quotas/service.py:resolve_hold_reason."""
    printer = await _get_printer_or_404(printer_id, db)
    if printer.is_virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A virtual queue has no real device to deliver a bypassed job to — it must "
                "always be held."
            ),
        )

    email = payload.user_email.strip().lower()
    roster_match = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email)
    )
    if roster_match.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.user_email}' is not in the synced Google Workspace user "
                "roster — sync Google Workspace settings, or double-check the address."
            ),
        )

    existing = await db.execute(
        select(PrinterReleaseBypass).where(
            PrinterReleaseBypass.printer_id == printer_id,
            PrinterReleaseBypass.user_email == email,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{payload.user_email}' already bypasses release on this printer.",
        )

    bypass = PrinterReleaseBypass(printer_id=printer_id, user_email=email)
    db.add(bypass)
    await db.commit()
    await db.refresh(bypass)
    return bypass


@router.delete(
    "/{printer_id}/release-bypasses/{bypass_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_printer_release_bypass(
    printer_id: UUID, bypass_id: UUID, db: AsyncSession = Depends(get_db)
):
    bypass = await db.get(PrinterReleaseBypass, bypass_id)
    if bypass is None or bypass.printer_id != printer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bypass not found")
    await db.delete(bypass)
    await db.commit()


@router.get("/{printer_id}/allowed-ous", response_model=list[PrinterAllowedOuOut])
async def list_printer_allowed_ous(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    await _get_printer_or_404(printer_id, db)
    result = await db.execute(
        select(PrinterAllowedOu)
        .where(PrinterAllowedOu.printer_id == printer_id)
        .order_by(PrinterAllowedOu.ou_path)
    )
    return result.scalars().all()


@router.post(
    "/{printer_id}/allowed-ous",
    response_model=PrinterAllowedOuOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_printer_allowed_ou(
    printer_id: UUID, payload: PrinterAllowedOuCreate, db: AsyncSession = Depends(get_db)
):
    """Restricts self-service web upload printing (app/self_service_print/)
    to this OU (and anything nested under it) — the printer's very first
    allowed-OU row is what flips it from open-to-everyone to restricted,
    see PrinterAllowedOu's docstring. Unrelated to normal AirPrint/MDM
    printing, which this never touches."""
    await _get_printer_or_404(printer_id, db)

    ou_path = payload.ou_path.strip()
    roster_match = await db.execute(
        select(GoogleWorkspaceUser).where(GoogleWorkspaceUser.org_unit_path == ou_path)
    )
    if roster_match.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.ou_path}' doesn't match any org unit in the synced Google "
                "Workspace roster — sync Google Workspace settings, or double-check the path."
            ),
        )

    existing = await db.execute(
        select(PrinterAllowedOu).where(
            PrinterAllowedOu.printer_id == printer_id,
            PrinterAllowedOu.ou_path == ou_path,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{payload.ou_path}' is already allowed on this printer.",
        )

    allowed = PrinterAllowedOu(printer_id=printer_id, ou_path=ou_path)
    db.add(allowed)
    await db.commit()
    await db.refresh(allowed)
    return allowed


@router.delete(
    "/{printer_id}/allowed-ous/{allowed_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_printer_allowed_ou(
    printer_id: UUID, allowed_id: UUID, db: AsyncSession = Depends(get_db)
):
    allowed = await db.get(PrinterAllowedOu, allowed_id)
    if allowed is None or allowed.printer_id != printer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowed OU not found")
    await db.delete(allowed)
    await db.commit()
