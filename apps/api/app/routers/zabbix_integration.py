from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import verify_zabbix_token
from app.models.printer import Printer
from app.reports.aggregation import ReportFilters, get_summary
from app.schemas.zabbix import ZabbixPrinterDetailOut, ZabbixSummaryOut

# Data-serving router for an external Zabbix server's own HTTP-agent polls
# — deliberately separate from app/routers/settings.py's Zabbix config
# CRUD (GET/PUT /api/v1/settings/zabbix), same split already used between
# jobs.py's backend-token router and its JWT-gated user_router. Nothing
# here is reachable without a valid, enabled Zabbix token — see
# app.deps.verify_zabbix_token.
router = APIRouter(dependencies=[Depends(verify_zabbix_token)])


@router.get("/summary", response_model=ZabbixSummaryOut)
async def zabbix_summary(db: AsyncSession = Depends(get_db)):
    """Rolling 24h fleet totals, recomputed fresh on every poll — not
    wall-clock "today" (which Insights/Live Dashboard use for a human
    glancing at "so far today"). A midnight reset would make Zabbix see an
    artificial sawtooth every night that has nothing to do with fleet
    health; a rolling window stays comparable regardless of what time a
    given poll happens to land on. See app/reports/aggregation.py's
    get_summary/SummaryTotals for the underlying query — cost/CO2/
    leaderboards/fun-facts are deliberately not exposed here, since
    they're derived/cosmetic rather than real monitoring signals."""
    end = datetime.now(UTC)
    start = end - timedelta(hours=24)
    totals = await get_summary(db, ReportFilters(start=start, end=end))
    return ZabbixSummaryOut(
        total_jobs=totals.total_jobs,
        forwarded_jobs=totals.forwarded_jobs,
        failed_jobs=totals.failed_jobs,
        cancelled_jobs=totals.cancelled_jobs,
        total_pages=totals.total_pages,
        color_pages=totals.color_pages,
        mono_pages=totals.mono_pages,
        duplex_pages=totals.duplex_pages,
        simplex_pages=totals.simplex_pages,
    )


@router.get("/printers")
async def zabbix_printer_discovery(db: AsyncSession = Depends(get_db)):
    """Zabbix Low-Level Discovery (LLD) endpoint — the response shape
    ({"data": [...]} wrapping flat objects keyed by literal "{#MACRO}"
    strings) is Zabbix's own LLD JSON convention, not a PrintOps schema,
    so the imported template's discovery rule can consume it directly
    with no lld_macro_paths mapping needed. Active printers only —
    archived ones (Printer.archived_at set) have no live CUPS queue to
    monitor."""
    result = await db.execute(
        select(Printer).where(Printer.archived_at.is_(None)).order_by(Printer.name)
    )
    return {
        "data": [
            {
                "{#PRINTER_ID}": str(printer.id),
                "{#PRINTER_NAME}": printer.name,
                "{#PRINTER_IP}": printer.ip_address,
                "{#PRINTER_BUILDING}": printer.building or "",
                "{#PRINTER_ROOM}": printer.room or "",
                "{#PRINTER_DEPARTMENT}": printer.department or "",
            }
            for printer in result.scalars().all()
        ]
    }


@router.get("/printers/{printer_id}", response_model=ZabbixPrinterDetailOut)
async def zabbix_printer_detail(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Per-printer current health/counters, polled by each discovered
    printer's master item-prototype in the template (see
    infra/zabbix/printops_template.yaml). Nullable text fields become ""
    rather than null so a Zabbix "not empty" trigger (e.g. on
    queue_sync_error) works without extra preprocessing — page_count_total
    stays genuinely null when unpolled, so that item goes "not supported"
    instead of silently recording a false zero."""
    result = await db.execute(
        select(Printer).where(Printer.id == printer_id, Printer.archived_at.is_(None))
    )
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")
    return ZabbixPrinterDetailOut(
        status=printer.status,
        status_reasons=", ".join(printer.status_reasons or []),
        queue_sync_error=printer.queue_sync_error or "",
        page_count_total=printer.page_count_total,
        page_count_confidence=printer.page_count_confidence or "",
        building=printer.building or "",
        room=printer.room or "",
        department=printer.department or "",
    )
