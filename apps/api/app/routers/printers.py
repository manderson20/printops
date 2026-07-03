import asyncio
import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.job import Job
from app.models.printer import Printer
from app.models.report import PrinterTonerCartridge
from app.printers.capabilities import parse_capabilities, sanitize_raw_attributes
from app.printers.ipp_client import PrinterProbeError, probe_printer
from app.printers.job_control import JobControlError, purge_cups_queue
from app.printers.queue_sync import QueueSyncError, remove_queue, sync_queue
from app.printers.status import refresh_printer_status
from app.printers.test_print import TestPrintError, submit_test_print
from app.schemas.auth import UserOut
from app.schemas.printer import PrinterCreate, PrinterMdmConnectionOut, PrinterOut, PrinterUpdate
from app.schemas.report import CartridgeIn, CartridgeOut

router = APIRouter(dependencies=[Depends(get_current_user)])

# Fields that affect the CUPS queue (device-uri, PPD, sharing, AirPrint
# advertisement) — an update touching only these should trigger a re-sync.
# Anything else (notes, department, building...) shouldn't cause CUPS/Avahi
# churn.
QUEUE_AFFECTING_FIELDS = {"name", "ip_address", "port", "use_tls", "ipp_path", "airprint_enabled"}


async def _get_printer_or_404(printer_id: UUID, db: AsyncSession) -> Printer:
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")
    return printer


async def _apply_discovery(printer: Printer) -> None:
    """Probes the printer's stored connection details and updates its capability fields."""
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


async def _apply_queue_sync(printer: Printer, db: AsyncSession) -> None:
    """Creates/updates the printer's CUPS queue to match its current
    connection details. Must run after the printer is already committed —
    the sync script reads connection info back via the internal API, which
    reads the DB. Non-fatal: failure is recorded on the printer, not raised,
    so a print-server hiccup doesn't block adding/editing a printer."""
    try:
        await asyncio.to_thread(sync_queue, str(printer.id))
        printer.queue_sync_error = None
    except QueueSyncError as exc:
        printer.queue_sync_error = str(exc)
    await db.commit()
    await db.refresh(printer)


@router.post(
    "", response_model=PrinterOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("admin"))]
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
    )
    await _apply_discovery(printer)
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    await _apply_queue_sync(printer, db)
    return printer


@router.get("", response_model=list[PrinterOut])
async def list_printers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Printer).order_by(Printer.name))
    return result.scalars().all()


@router.get("/{printer_id}", response_model=PrinterOut)
async def get_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    return await _get_printer_or_404(printer_id, db)


@router.patch("/{printer_id}", response_model=PrinterOut, dependencies=[Depends(require_role("admin"))])
async def update_printer(
    printer_id: UUID, payload: PrinterUpdate, db: AsyncSession = Depends(get_db)
):
    printer = await _get_printer_or_404(printer_id, db)
    updates = payload.model_dump(exclude_unset=True)
    if "ip_address" in updates and updates["ip_address"] is not None:
        updates["ip_address"] = str(updates["ip_address"])
    for field, value in updates.items():
        setattr(printer, field, value)
    # First time release is turned on for this printer, it needs a token to
    # exist at all — generated here rather than requiring a separate manual
    # step before the toggle does anything useful. Regenerating an existing
    # one (e.g. a lost/reissued kiosk) is POST /{id}/regenerate-release-token.
    if printer.release_required and not printer.release_token:
        printer.release_token = secrets.token_urlsafe(16)
    await db.commit()
    await db.refresh(printer)
    if QUEUE_AFFECTING_FIELDS & updates.keys():
        await _apply_queue_sync(printer, db)
    return printer


@router.delete(
    "/{printer_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_role("admin"))]
)
async def delete_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    printer = await _get_printer_or_404(printer_id, db)
    try:
        await asyncio.to_thread(remove_queue, str(printer.id))
    except QueueSyncError:
        pass  # best-effort — don't block a delete the admin explicitly asked for
    await db.delete(printer)
    await db.commit()


@router.post("/{printer_id}/discover", response_model=PrinterOut, dependencies=[Depends(require_role("admin"))])
async def discover_printer(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    printer = await _get_printer_or_404(printer_id, db)
    await _apply_discovery(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


@router.post("/{printer_id}/resync-queue", response_model=PrinterOut, dependencies=[Depends(require_role("admin"))])
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
    — same underlying probe as the 60s background loop (app/main.py), just
    triggered immediately instead of waiting for the next cycle. Read-only
    telemetry, so open to any logged-in user (not admin-gated) like GET."""
    printer = await _get_printer_or_404(printer_id, db)
    await refresh_printer_status(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


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
    printer, since clients print through PrintOps."""
    printer = await _get_printer_or_404(printer_id, db)
    queue_name = f"printops-{printer.id}"
    resource_path = f"/printers/{queue_name}"
    return PrinterMdmConnectionOut(
        queue_name=queue_name,
        host=settings.print_server_host,
        port=settings.print_server_port,
        resource_path=resource_path,
        ipp_uri=f"ipp://{settings.print_server_host}:{settings.print_server_port}{resource_path}",
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

    await db.execute(
        PrinterTonerCartridge.__table__.delete().where(
            PrinterTonerCartridge.printer_id == printer_id
        )
    )
    for entry in payload:
        db.add(
            PrinterTonerCartridge(
                printer_id=printer_id,
                color=entry.color,
                cost=entry.cost,
                yield_pages=entry.yield_pages,
            )
        )
    await db.commit()

    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer_id)
    )
    return result.scalars().all()
