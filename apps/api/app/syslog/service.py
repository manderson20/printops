from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.printer import Printer
from app.models.syslog import SEVERITY_ORDER, PrinterSyslogEvent, SyslogSettings
from app.schemas.syslog import (
    SyslogEventIngest,
    SyslogEventListOut,
    SyslogEventOut,
    SyslogIngestResult,
)


async def get_or_create_syslog_settings(db: AsyncSession) -> SyslogSettings:
    result = await db.execute(select(SyslogSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = SyslogSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _meets_floor(severity: str | None, min_severity: str) -> bool:
    """A message with no parseable severity is kept regardless of the
    floor — the relay couldn't extract a <PRI> tag, which is itself
    unusual enough to be worth keeping rather than silently guessing it
    away as routine noise."""
    if severity is None:
        return True
    try:
        return SEVERITY_ORDER.index(severity) <= SEVERITY_ORDER.index(min_severity)
    except ValueError:
        return True


async def ingest_events(
    db: AsyncSession, settings: SyslogSettings, events: list[SyslogEventIngest]
) -> SyslogIngestResult:
    """Matches each event's source_ip to a Printer (None if unmatched —
    still stored, see PrinterSyslogEvent's docstring) and applies the
    min_severity noise floor. Caller (app/routers/internal.py) commits."""
    if not settings.enabled or not events:
        return SyslogIngestResult(accepted=0, dropped=len(events))

    source_ips = {event.source_ip for event in events}
    result = await db.execute(
        select(Printer.id, Printer.ip_address).where(Printer.ip_address.in_(source_ips))
    )
    printer_by_ip = {ip: printer_id for printer_id, ip in result.all()}

    accepted = 0
    dropped = 0
    for event in events:
        if not _meets_floor(event.severity, settings.min_severity):
            dropped += 1
            continue
        db.add(
            PrinterSyslogEvent(
                printer_id=printer_by_ip.get(event.source_ip),
                source_ip=event.source_ip,
                received_at=event.received_at,
                device_timestamp=event.device_timestamp,
                severity=event.severity,
                facility=event.facility,
                hostname=event.hostname,
                app_name=event.app_name,
                message=event.message,
                raw=event.raw,
            )
        )
        accepted += 1

    return SyslogIngestResult(accepted=accepted, dropped=dropped)


async def list_events(
    db: AsyncSession,
    *,
    printer_id: UUID | None = None,
    severity: str | None = None,
    unmatched_only: bool = False,
    search: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SyslogEventListOut], int]:
    """Backs both the per-printer (GET /printers/{id}/syslog) and
    fleet-wide (GET /syslog) list endpoints — the fleet-wide one is just
    this with printer_id left unset. Same offset/page_size pagination
    convention as app/routers/users.py:list_users."""
    filters = []
    if printer_id is not None:
        filters.append(PrinterSyslogEvent.printer_id == printer_id)
    if unmatched_only:
        filters.append(PrinterSyslogEvent.printer_id.is_(None))
    if severity is not None:
        filters.append(PrinterSyslogEvent.severity == severity)
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(PrinterSyslogEvent.message.ilike(pattern), PrinterSyslogEvent.raw.ilike(pattern))
        )
    if since is not None:
        filters.append(PrinterSyslogEvent.received_at >= since)
    if until is not None:
        filters.append(PrinterSyslogEvent.received_at <= until)

    count_stmt = select(func.count()).select_from(PrinterSyslogEvent)
    items_stmt = (
        select(PrinterSyslogEvent, Printer.name)
        .outerjoin(Printer, PrinterSyslogEvent.printer_id == Printer.id)
        .order_by(PrinterSyslogEvent.received_at.desc())
    )
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        items_stmt = items_stmt.where(condition)

    total = (await db.execute(count_stmt)).scalar_one()
    items_stmt = items_stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(items_stmt)).all()

    items = [
        SyslogEventListOut(
            **SyslogEventOut.model_validate(event).model_dump(), printer_name=printer_name
        )
        for event, printer_name in rows
    ]
    return items, total
