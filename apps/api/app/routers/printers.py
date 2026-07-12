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
from app.models.quota import PrinterUserQuota
from app.models.release_bypass import PrinterReleaseBypass
from app.models.report import PrinterTonerCartridge
from app.printers.counter_history import get_daily_deltas
from app.printers.discovery import refresh_printer_capabilities
from app.printers.job_control import JobControlError, purge_cups_queue
from app.printers.queue_sync import QueueSyncError, remove_queue, sync_queue
from app.printers.snmp_counters import (
    SnmpProbeError,
    get_or_create_snmp_defaults,
    get_toner_supplies,
    record_reading,
    refresh_printer_counters,
    resolve_snmp_config,
)
from app.printers.status import refresh_printer_status_and_rediscover
from app.printers.test_print import TestPrintError, submit_test_print
from app.quotas.service import get_pages_used, period_bounds
from app.schemas.auth import UserOut
from app.schemas.printer import (
    PrinterCreate,
    PrinterMdmConnectionOut,
    PrinterOut,
    PrinterUpdate,
    VirtualQueueCreate,
)
from app.schemas.quota import PrinterUserQuotaCreate, PrinterUserQuotaOut, PrinterUserQuotaUpdate
from app.schemas.release_bypass import PrinterReleaseBypassCreate, PrinterReleaseBypassOut
from app.schemas.report import (
    CartridgeIn,
    CartridgeOut,
    DetectCartridgesResult,
    DetectedSupplyOut,
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
        toner_cartridge_model=payload.toner_cartridge_model,
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

    # Carry over each color's detected_* fields (app/printers/snmp_counters.py:
    # get_toner_supplies via the /detect endpoint below) across this
    # delete-and-recreate — an admin correcting the cost/yield_pages an
    # SNMP detect just surfaced shouldn't wipe that same detect's result.
    existing = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    detected_by_color = {
        row.color: (row.detected_description, row.detected_high_capacity, row.detected_at)
        for row in existing.scalars().all()
    }

    await db.execute(
        PrinterTonerCartridge.__table__.delete().where(
            PrinterTonerCartridge.printer_id == printer_id
        )
    )
    for entry in payload:
        detected_description, detected_high_capacity, detected_at = detected_by_color.get(
            entry.color, (None, None, None)
        )
        db.add(
            PrinterTonerCartridge(
                printer_id=printer_id,
                color=entry.color,
                cost=entry.cost,
                yield_pages=entry.yield_pages,
                detected_description=detected_description,
                detected_high_capacity=detected_high_capacity,
                detected_at=detected_at,
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
    confirmed) and upserts detected_* onto each matched color's row —
    cost/yield_pages are left untouched (SNMP has no concept of a dollar
    cost), creating a new row with cost=0/yield_pages=0 for any detected
    color that doesn't have one yet. compute_printer_rate already treats
    yield_pages=0 as "not configured" and falls back to the flat rate, so
    a freshly-detected, not-yet-priced row is safe, not a silent zero
    cost. Supplies the probe saw but couldn't confidently color-match are
    returned in `unmatched` rather than dropped."""
    printer = await _get_printer_or_404(printer_id, db)
    defaults = await get_or_create_snmp_defaults(db)
    config = resolve_snmp_config(printer, defaults)

    try:
        supplies = await asyncio.to_thread(get_toner_supplies, printer.ip_address, config)
    except SnmpProbeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    existing = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    rows_by_color = {row.color: row for row in existing.scalars().all()}

    now = datetime.now(UTC)
    unmatched: list[DetectedSupplyOut] = []
    for supply in supplies:
        if supply.color is None:
            unmatched.append(DetectedSupplyOut(**vars(supply)))
            continue
        row = rows_by_color.get(supply.color)
        if row is None:
            row = PrinterTonerCartridge(
                printer_id=printer_id, color=supply.color, cost=0.0, yield_pages=0
            )
            db.add(row)
            rows_by_color[supply.color] = row
        row.detected_description = supply.description
        row.detected_high_capacity = supply.high_capacity
        row.detected_at = now

    await db.commit()

    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
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
