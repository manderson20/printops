from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.schemas.syslog import SyslogEventPage
from app.syslog.service import list_events

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("", response_model=SyslogEventPage)
async def list_syslog_events(
    printer_id: UUID | None = Query(None),
    severity: str | None = Query(None),
    unmatched_only: bool = Query(False),
    search: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Fleet-wide syslog view — same underlying query as GET
    /printers/{id}/syslog (app/routers/printers.py), just without a fixed
    printer_id, plus an unmatched_only toggle for spotting devices sending
    syslog here that aren't a registered Printer yet (see
    app/models/syslog.py's PrinterSyslogEvent docstring)."""
    items, total = await list_events(
        db,
        printer_id=printer_id,
        severity=severity,
        unmatched_only=unmatched_only,
        search=search,
        since=since,
        until=until,
        page=page,
        page_size=page_size,
    )
    return SyslogEventPage(items=items, total=total, page=page, page_size=page_size)
