import csv
import io
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.report import ReportFormulaSettings, ReportSnapshot
from app.reports.aggregation import (
    ReportFilters,
    get_peak_times,
    get_printer_leaderboard,
    get_raw_rows_for_export,
    get_summary,
    get_timeline,
    get_user_leaderboard,
)
from app.reports.formulas import FormulaValues, compute_environmental_impact
from app.reports.fun_facts import generate_fun_facts
from app.schemas.auth import UserOut
from app.schemas.report import (
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


def _build_summary_out(summary, environmental) -> SummaryOut:
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
        estimated_cost_mono=environmental.estimated_cost_mono,
        estimated_cost_color=environmental.estimated_cost_color,
        estimated_cost_total=environmental.estimated_cost_total,
        sheets_of_paper=environmental.sheets_of_paper,
        duplex_sheets_saved=environmental.duplex_sheets_saved,
        trees_used=environmental.trees_used,
        co2_grams=environmental.co2_grams,
    )


async def _summary_out(db: AsyncSession, filters: ReportFilters) -> SummaryOut:
    summary = await get_summary(db, filters)
    formulas = _formula_values(await _get_or_create_formula_settings(db))
    environmental = compute_environmental_impact(summary, formulas)
    return _build_summary_out(summary, environmental)


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
    formulas = _formula_values(await _get_or_create_formula_settings(db))
    environmental = compute_environmental_impact(summary, formulas)
    summary_out = _build_summary_out(summary, environmental)
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
