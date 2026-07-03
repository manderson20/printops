import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.report import PrinterTonerCartridge, ReportFormulaSettings, ReportSnapshot
from app.reports.aggregation import (
    CostRawRow,
    ReportFilters,
    get_cost_raw_rows,
    get_peak_times,
    get_printer_leaderboard,
    get_raw_rows_for_export,
    get_summary,
    get_timeline,
    get_user_leaderboard,
)
from app.reports.formulas import (
    FormulaValues,
    JobCost,
    PrinterTonerRate,
    compute_environmental_impact,
    compute_printer_rate,
    job_cost,
)
from app.reports.fun_facts import generate_fun_facts
from app.schemas.auth import UserOut
from app.schemas.report import (
    CostEntryOut,
    FunFactsOut,
    LeaderboardEntryOut,
    PeakTimesOut,
    SnapshotCreate,
    SnapshotOut,
    SummaryOut,
    TimelineBucketOut,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _report_filters(
    start: datetime | None = None,
    end: datetime | None = None,
    building: str | None = None,
    department: str | None = None,
    printer_id: UUID | None = None,
    submitted_by: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    color_mode: str | None = None,
    duplex: bool | None = None,
    current_user: UserOut = Depends(get_current_user),
) -> ReportFilters:
    """Shared query-param parsing + RBAC scoping for every report endpoint.
    A non-admin only ever sees their own print history — `submitted_by` is
    force-set to their own identity (the same value Job.submitted_by stores
    for them, since UserOut.username *is* their attributed email for SSO
    logins — see app/schemas/auth.py), overriding whatever they passed."""
    if current_user.role != "admin":
        submitted_by = current_user.username
    return ReportFilters(
        start=start,
        end=end,
        building=building,
        department=department,
        printer_id=printer_id,
        submitted_by=submitted_by,
        status=status_filter,
        color_mode=color_mode,
        duplex=duplex,
    )


async def _get_or_create_formula_settings(db: AsyncSession) -> ReportFormulaSettings:
    result = await db.execute(select(ReportFormulaSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ReportFormulaSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _formula_values(settings: ReportFormulaSettings) -> FormulaValues:
    return FormulaValues(
        cost_per_page_mono=settings.cost_per_page_mono,
        cost_per_page_color=settings.cost_per_page_color,
        sheets_per_tree=settings.sheets_per_tree,
        co2_grams_per_sheet=settings.co2_grams_per_sheet,
    )


@dataclass
class _CostAccumulator:
    """Running per-group totals built one job row at a time — see
    _compute_cost_accumulators. mono/color toner cost are tracked
    separately (not just a combined `toner_cost`) so SummaryOut can still
    show the existing mono/color cost split, now sourced from real
    per-printer/per-job pricing instead of a single flat rate."""

    label: str
    job_count: int = 0
    page_count: int = 0
    mono_toner_cost: float = 0.0
    color_toner_cost: float = 0.0
    paper_cost: float = 0.0

    @property
    def toner_cost(self) -> float:
        return self.mono_toner_cost + self.color_toner_cost

    @property
    def total_cost(self) -> float:
        return self.toner_cost + self.paper_cost


def _accumulate(entry: _CostAccumulator, row: CostRawRow, cost: JobCost) -> None:
    entry.job_count += 1
    entry.page_count += row.page_count
    if row.color_mode == "color":
        entry.color_toner_cost += cost.toner_cost
    else:
        entry.mono_toner_cost += cost.toner_cost
    entry.paper_cost += cost.paper_cost


async def _load_printer_rates(
    db: AsyncSession, printer_ids: set[UUID], fallback: FormulaValues
) -> dict[UUID, PrinterTonerRate]:
    """One printer's cartridges price both its mono and color pages — see
    app/reports/formulas.py:compute_printer_rate for the fallback rule
    when a printer has no (or incomplete) cartridges configured yet."""
    if not printer_ids:
        return {}
    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id.in_(printer_ids))
    )
    by_printer: dict[UUID, list[PrinterTonerCartridge]] = {}
    for cartridge in result.scalars().all():
        by_printer.setdefault(cartridge.printer_id, []).append(cartridge)
    return {
        printer_id: compute_printer_rate(by_printer.get(printer_id, []), fallback)
        for printer_id in printer_ids
    }


async def _compute_cost_accumulators(
    db: AsyncSession,
    filters: ReportFilters,
    cost_per_sheet_paper: float,
    fallback: FormulaValues,
) -> tuple[dict[str, _CostAccumulator], dict[str, _CostAccumulator], _CostAccumulator]:
    """Returns (by_printer, by_user, overall) computed together in one pass
    over the raw job rows, since every grouping needs the identical
    per-job cost (real per-printer toner rate + physical-sheet-accurate
    paper cost — see the module docstring in aggregation.py's
    get_cost_raw_rows for why this can't be a single SQL GROUP BY)."""
    rows = await get_cost_raw_rows(db, filters)
    printer_ids = {r.printer_id for r in rows}
    rates = await _load_printer_rates(db, printer_ids, fallback)

    by_printer: dict[str, _CostAccumulator] = {}
    by_user: dict[str, _CostAccumulator] = {}
    overall = _CostAccumulator(label="Overall")

    for row in rows:
        rate = rates[row.printer_id]
        cost = job_cost(row.page_count, row.color_mode, row.duplex, rate, cost_per_sheet_paper)

        printer_entry = by_printer.setdefault(
            str(row.printer_id), _CostAccumulator(label=row.printer_name)
        )
        _accumulate(printer_entry, row, cost)

        if row.submitted_by:
            user_entry = by_user.setdefault(
                row.submitted_by, _CostAccumulator(label=row.submitted_by)
            )
            _accumulate(user_entry, row, cost)

        _accumulate(overall, row, cost)

    return by_printer, by_user, overall


def _build_summary_out(summary, environmental, cost_overall: _CostAccumulator) -> SummaryOut:
    return SummaryOut(
        total_jobs=summary.total_jobs,
        forwarded_jobs=summary.forwarded_jobs,
        failed_jobs=summary.failed_jobs,
        cancelled_jobs=summary.cancelled_jobs,
        total_pages=summary.total_pages,
        color_pages=summary.color_pages,
        mono_pages=summary.mono_pages,
        unknown_color_mode_pages=summary.unknown_color_mode_pages,
        duplex_pages=summary.duplex_pages,
        simplex_pages=summary.simplex_pages,
        unknown_duplex_pages=summary.unknown_duplex_pages,
        estimated_cost_mono=round(cost_overall.mono_toner_cost, 2),
        estimated_cost_color=round(cost_overall.color_toner_cost, 2),
        estimated_cost_paper=round(cost_overall.paper_cost, 2),
        estimated_cost_total=round(cost_overall.total_cost, 2),
        sheets_of_paper=environmental.sheets_of_paper,
        duplex_sheets_saved=environmental.duplex_sheets_saved,
        trees_used=environmental.trees_used,
        co2_grams=environmental.co2_grams,
    )


async def _summary_out(db: AsyncSession, filters: ReportFilters) -> SummaryOut:
    summary = await get_summary(db, filters)
    formula_settings = await _get_or_create_formula_settings(db)
    formulas = _formula_values(formula_settings)
    # sheets_of_paper/trees/co2 stay aggregate-based (unaffected by
    # per-printer cartridge cost) — only the dollar cost fields switch to
    # the new real, per-job-accurate calculation below.
    environmental = compute_environmental_impact(summary, formulas)
    _, _, overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, formulas
    )
    return _build_summary_out(summary, environmental, overall)


@router.get("/summary", response_model=SummaryOut)
async def report_summary(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    return await _summary_out(db, filters)


@router.get("/timeline", response_model=list[TimelineBucketOut])
async def report_timeline(
    granularity: str = "day",
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    if granularity not in ("day", "week", "month"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="granularity must be one of: day, week, month",
        )
    buckets = await get_timeline(db, filters, granularity=granularity)
    return [TimelineBucketOut(**vars(b)) for b in buckets]


@router.get("/leaderboard", response_model=list[LeaderboardEntryOut])
async def report_leaderboard(
    type: str = "printer",
    limit: int = 10,
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    if type not in ("printer", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="type must be 'printer' or 'user'"
        )
    entries = (
        await get_printer_leaderboard(db, filters, limit=min(limit, 50))
        if type == "printer"
        else await get_user_leaderboard(db, filters, limit=min(limit, 50))
    )
    return [LeaderboardEntryOut(**vars(e)) for e in entries]


@router.get("/cost-breakdown", response_model=list[CostEntryOut])
async def report_cost_breakdown(
    group_by: str = "printer",
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    """Real per-printer/per-job cost, grouped by printer or by user — the
    "cost by user" report. Supersedes /leaderboard's job_count/total_pages
    for display purposes (this returns both, plus cost), but /leaderboard
    stays for callers that only need the lighter query."""
    if group_by not in ("printer", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="group_by must be 'printer' or 'user'"
        )
    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    by_printer, by_user, _overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, fallback
    )
    buckets = by_printer if group_by == "printer" else by_user
    entries = [
        CostEntryOut(
            key=key,
            label=acc.label,
            job_count=acc.job_count,
            page_count=acc.page_count,
            toner_cost=round(acc.toner_cost, 2),
            paper_cost=round(acc.paper_cost, 2),
            total_cost=round(acc.total_cost, 2),
        )
        for key, acc in buckets.items()
    ]
    entries.sort(key=lambda e: e.total_cost, reverse=True)
    return entries


@router.get("/peak-times", response_model=PeakTimesOut)
async def report_peak_times(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    peak = await get_peak_times(db, filters)
    return PeakTimesOut(by_day_of_week=peak.by_day_of_week, by_hour=peak.by_hour)


def _previous_period_filters(filters: ReportFilters) -> ReportFilters | None:
    """The same-length period immediately before the current one, for the
    "printed X% more/less than last <period>" fact — only meaningful when
    both ends of the current range are known."""
    if filters.start is None or filters.end is None:
        return None
    duration = filters.end - filters.start
    if duration.total_seconds() <= 0:
        return None
    previous_end = filters.start
    previous_start = previous_end - duration
    return ReportFilters(
        start=previous_start,
        end=previous_end,
        building=filters.building,
        department=filters.department,
        printer_id=filters.printer_id,
        submitted_by=filters.submitted_by,
        status=filters.status,
        color_mode=filters.color_mode,
        duplex=filters.duplex,
    )


@router.get("/fun-facts", response_model=FunFactsOut)
async def report_fun_facts(
    period_label: str = "period",
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    summary = await get_summary(db, filters)
    timeline = await get_timeline(db, filters, granularity="day")
    peak_times = await get_peak_times(db, filters)
    printer_leaderboard = await get_printer_leaderboard(db, filters)
    formulas = _formula_values(await _get_or_create_formula_settings(db))
    environmental = compute_environmental_impact(summary, formulas)

    previous_filters = _previous_period_filters(filters)
    previous_summary = await get_summary(db, previous_filters) if previous_filters else None

    facts = generate_fun_facts(
        summary,
        timeline,
        peak_times,
        printer_leaderboard,
        environmental,
        previous_summary=previous_summary,
        period_label=period_label,
    )
    return FunFactsOut(facts=facts)


@router.get("/export.csv")
async def export_csv(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    rows = await get_raw_rows_for_export(db, filters)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "job_id",
            "printer_name",
            "submitted_by",
            "document_name",
            "status",
            "page_count",
            "copy_count",
            "color_mode",
            "duplex",
            "paper_size",
            "file_size_bytes",
            "submitted_at",
            "completed_at",
        ]
    )
    for job, printer_name in rows:
        writer.writerow(
            [
                job.id,
                printer_name,
                job.submitted_by or "",
                job.document_name or "",
                job.status,
                job.page_count if job.page_count is not None else "",
                job.copy_count if job.copy_count is not None else "",
                job.color_mode or "",
                job.duplex if job.duplex is not None else "",
                job.paper_size or "",
                job.file_size_bytes if job.file_size_bytes is not None else "",
                job.created_at.isoformat(),
                job.completed_at.isoformat() if job.completed_at else "",
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=print-insights-export.csv"},
    )


@router.get(
    "/snapshots", response_model=list[SnapshotOut], dependencies=[Depends(require_role("admin"))]
)
async def list_snapshots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ReportSnapshot).order_by(ReportSnapshot.created_at.desc()))
    return result.scalars().all()


@router.post(
    "/snapshots",
    response_model=SnapshotOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
async def create_snapshot(
    payload: SnapshotCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Computes totals + fun facts right now and freezes them — see
    ReportSnapshot's docstring (app/models/report.py) for why this doesn't
    just store the filters and recompute later."""
    range_start_dt = datetime.combine(payload.range_start, datetime.min.time())
    range_end_dt = datetime.combine(payload.range_end, datetime.min.time()) + timedelta(days=1)
    filters = ReportFilters(
        start=range_start_dt,
        end=range_end_dt,
        building=payload.filters.building,
        department=payload.filters.department,
        printer_id=payload.filters.printer_id,
        submitted_by=payload.filters.submitted_by,
        status=payload.filters.status,
        color_mode=payload.filters.color_mode,
        duplex=payload.filters.duplex,
    )

    summary = await get_summary(db, filters)
    timeline = await get_timeline(db, filters, granularity="day")
    peak_times = await get_peak_times(db, filters)
    printer_leaderboard = await get_printer_leaderboard(db, filters)
    formula_settings = await _get_or_create_formula_settings(db)
    formulas = _formula_values(formula_settings)
    environmental = compute_environmental_impact(summary, formulas)
    _, _, cost_overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, formulas
    )
    summary_out = _build_summary_out(summary, environmental, cost_overall)
    previous_filters = _previous_period_filters(filters)
    previous_summary = await get_summary(db, previous_filters) if previous_filters else None
    facts = generate_fun_facts(
        summary,
        timeline,
        peak_times,
        printer_leaderboard,
        environmental,
        previous_summary=previous_summary,
        period_label=payload.period_label,
    )

    snapshot = ReportSnapshot(
        name=payload.name,
        range_start=payload.range_start,
        range_end=payload.range_end,
        filters=payload.filters.model_dump(mode="json"),
        totals=summary_out.model_dump(),
        fun_facts=facts,
        created_by=current_user.username,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=SnapshotOut,
    dependencies=[Depends(require_role("admin"))],
)
async def get_snapshot(snapshot_id: UUID, db: AsyncSession = Depends(get_db)):
    snapshot = await db.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    return snapshot


@router.delete(
    "/snapshots/{snapshot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_snapshot(snapshot_id: UUID, db: AsyncSession = Depends(get_db)):
    snapshot = await db.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    await db.delete(snapshot)
    await db.commit()
