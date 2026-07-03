"""Backend aggregation for Print Insights — every number the frontend shows
comes from a query in this module, never from summing raw rows client-side.

Two aggregation styles are used deliberately:
- Summary and leaderboard totals are computed with real SQL
  aggregates (COUNT/SUM/CASE-WHEN, GROUP BY) — clean and portable.
- Timeline (day/week/month buckets) and peak-times (day-of-week/hour) need
  date-truncation that isn't expressible the same way across SQLite (tests)
  and Postgres (production) without dialect-branching every query. Those two
  instead pull a minimal filtered row set in one query and bucket in Python
  — still entirely server-side, just not a single GROUP BY statement.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer

TERMINAL_STATUSES = ("forwarded", "failed", "cancelled")
Granularity = str  # "day" | "week" | "month"


@dataclass
class ReportFilters:
    start: datetime | None = None
    end: datetime | None = None
    building: str | None = None
    department: str | None = None
    printer_id: UUID | None = None
    submitted_by: str | None = None
    status: str | None = None
    color_mode: str | None = None
    duplex: bool | None = None


def _apply_filters(stmt: Select, filters: ReportFilters) -> Select:
    """Applied identically everywhere a filtered job query is built, so
    every report endpoint agrees on what a given filter set means."""
    stmt = stmt.join(Printer, Job.printer_id == Printer.id)
    if filters.start is not None:
        stmt = stmt.where(Job.created_at >= filters.start)
    if filters.end is not None:
        stmt = stmt.where(Job.created_at < filters.end)
    if filters.building is not None:
        stmt = stmt.where(Printer.building == filters.building)
    if filters.department is not None:
        stmt = stmt.where(Printer.department == filters.department)
    if filters.printer_id is not None:
        stmt = stmt.where(Job.printer_id == filters.printer_id)
    if filters.submitted_by is not None:
        stmt = stmt.where(Job.submitted_by == filters.submitted_by)
    if filters.status is not None:
        stmt = stmt.where(Job.status == filters.status)
    if filters.color_mode is not None:
        stmt = stmt.where(Job.color_mode == filters.color_mode)
    if filters.duplex is not None:
        stmt = stmt.where(Job.duplex == filters.duplex)
    return stmt


@dataclass
class SummaryTotals:
    total_jobs: int = 0
    forwarded_jobs: int = 0
    failed_jobs: int = 0
    cancelled_jobs: int = 0
    total_pages: int = 0
    color_pages: int = 0
    mono_pages: int = 0
    unknown_color_mode_pages: int = 0
    duplex_pages: int = 0
    simplex_pages: int = 0
    unknown_duplex_pages: int = 0


async def get_summary(db: AsyncSession, filters: ReportFilters) -> SummaryTotals:
    pages = func.coalesce(Job.page_count, 0)
    stmt = _apply_filters(
        select(
            func.count(Job.id).label("total_jobs"),
            func.sum(func.coalesce(Job.page_count, 0)).label("total_pages"),
            func.count().filter(Job.status == "forwarded").label("forwarded_jobs"),
            func.count().filter(Job.status == "failed").label("failed_jobs"),
            func.count().filter(Job.status == "cancelled").label("cancelled_jobs"),
            func.sum(pages).filter(Job.color_mode == "color").label("color_pages"),
            func.sum(pages).filter(Job.color_mode == "monochrome").label("mono_pages"),
            func.sum(pages).filter(Job.color_mode.is_(None)).label("unknown_color_mode_pages"),
            func.sum(pages).filter(Job.duplex.is_(True)).label("duplex_pages"),
            func.sum(pages).filter(Job.duplex.is_(False)).label("simplex_pages"),
            func.sum(pages).filter(Job.duplex.is_(None)).label("unknown_duplex_pages"),
        ),
        filters,
    )
    row = (await db.execute(stmt)).one()
    return SummaryTotals(
        total_jobs=row.total_jobs or 0,
        forwarded_jobs=row.forwarded_jobs or 0,
        failed_jobs=row.failed_jobs or 0,
        cancelled_jobs=row.cancelled_jobs or 0,
        total_pages=row.total_pages or 0,
        color_pages=row.color_pages or 0,
        mono_pages=row.mono_pages or 0,
        unknown_color_mode_pages=row.unknown_color_mode_pages or 0,
        duplex_pages=row.duplex_pages or 0,
        simplex_pages=row.simplex_pages or 0,
        unknown_duplex_pages=row.unknown_duplex_pages or 0,
    )


@dataclass
class _RawRow:
    created_at: datetime
    page_count: int
    color_mode: str | None
    duplex: bool | None


async def _fetch_raw_rows(db: AsyncSession, filters: ReportFilters) -> list[_RawRow]:
    stmt = _apply_filters(
        select(Job.created_at, Job.page_count, Job.color_mode, Job.duplex), filters
    )
    rows = (await db.execute(stmt)).all()
    return [
        _RawRow(
            created_at=r.created_at,
            page_count=r.page_count or 0,
            color_mode=r.color_mode,
            duplex=r.duplex,
        )
        for r in rows
    ]


def _bucket_key(dt: datetime, granularity: Granularity) -> date:
    d = dt.date()
    if granularity == "day":
        return d
    if granularity == "week":
        return d.fromordinal(d.toordinal() - d.weekday())  # Monday of that week
    if granularity == "month":
        return d.replace(day=1)
    raise ValueError(f"Unknown granularity: {granularity!r}")


@dataclass
class TimelineBucket:
    bucket_start: date
    total_pages: int = 0
    color_pages: int = 0
    mono_pages: int = 0
    duplex_pages: int = 0
    simplex_pages: int = 0
    job_count: int = 0


async def get_timeline(
    db: AsyncSession, filters: ReportFilters, granularity: Granularity = "day"
) -> list[TimelineBucket]:
    rows = await _fetch_raw_rows(db, filters)
    buckets: dict[date, TimelineBucket] = {}
    for r in rows:
        key = _bucket_key(r.created_at, granularity)
        bucket = buckets.setdefault(key, TimelineBucket(bucket_start=key))
        bucket.job_count += 1
        bucket.total_pages += r.page_count
        if r.color_mode == "color":
            bucket.color_pages += r.page_count
        elif r.color_mode == "monochrome":
            bucket.mono_pages += r.page_count
        if r.duplex is True:
            bucket.duplex_pages += r.page_count
        elif r.duplex is False:
            bucket.simplex_pages += r.page_count
    return sorted(buckets.values(), key=lambda b: b.bucket_start)


@dataclass
class PeakTimes:
    by_day_of_week: dict[int, int] = field(default_factory=dict)  # 0=Monday .. 6=Sunday
    by_hour: dict[int, int] = field(default_factory=dict)  # 0..23


async def get_peak_times(db: AsyncSession, filters: ReportFilters) -> PeakTimes:
    rows = await _fetch_raw_rows(db, filters)
    by_day = Counter()
    by_hour = Counter()
    for r in rows:
        by_day[r.created_at.weekday()] += r.page_count
        by_hour[r.created_at.hour] += r.page_count
    return PeakTimes(by_day_of_week=dict(by_day), by_hour=dict(by_hour))


@dataclass
class LeaderboardEntry:
    key: str  # printer_id (str) or submitted_by
    label: str  # printer name or submitted_by (same as key for users)
    job_count: int
    total_pages: int


async def get_printer_leaderboard(
    db: AsyncSession, filters: ReportFilters, limit: int = 10
) -> list[LeaderboardEntry]:
    stmt = _apply_filters(
        select(
            Job.printer_id,
            Printer.name,
            func.count(Job.id).label("job_count"),
            func.sum(func.coalesce(Job.page_count, 0)).label("total_pages"),
        ),
        filters,
    ).group_by(Job.printer_id, Printer.name).order_by(func.count(Job.id).desc()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        LeaderboardEntry(
            key=str(r.printer_id),
            label=r.name,
            job_count=r.job_count,
            total_pages=r.total_pages or 0,
        )
        for r in rows
    ]


async def get_user_leaderboard(
    db: AsyncSession, filters: ReportFilters, limit: int = 10
) -> list[LeaderboardEntry]:
    stmt = _apply_filters(
        select(
            Job.submitted_by,
            func.count(Job.id).label("job_count"),
            func.sum(func.coalesce(Job.page_count, 0)).label("total_pages"),
        ),
        filters,
    ).where(Job.submitted_by.is_not(None)).group_by(Job.submitted_by).order_by(
        func.count(Job.id).desc()
    ).limit(limit)
    rows = (await db.execute(stmt)).all()
    return [
        LeaderboardEntry(
            key=r.submitted_by,
            label=r.submitted_by,
            job_count=r.job_count,
            total_pages=r.total_pages or 0,
        )
        for r in rows
    ]


def physical_sheets_used(page_count: int, duplex: bool | None) -> int:
    """Sheets actually consumed by a job — half the page count (rounded up)
    if printed duplex, else one sheet per page. An unknown duplex flag is
    treated as simplex (conservative — doesn't overstate paper savings)."""
    if duplex:
        return math.ceil(page_count / 2)
    return page_count


@dataclass
class CostRawRow:
    """One job's worth of the fields needed to price it — printer identity
    is included because toner rate is per-printer (see
    app/reports/formulas.py:compute_printer_rate), unlike the plain
    _RawRow above which only timeline/peak-times need."""

    printer_id: UUID
    printer_name: str
    submitted_by: str | None
    page_count: int
    color_mode: str | None
    duplex: bool | None


async def get_cost_raw_rows(db: AsyncSession, filters: ReportFilters) -> list[CostRawRow]:
    """Feeds real per-job cost calculation (app/routers/reports.py's
    cost-breakdown endpoint) — kept in this module rather than computing
    cost here directly so aggregation.py stays DB-query-only and
    app/reports/formulas.py stays a pure, DB-free calculation module (it
    already imports from here; importing back would be circular)."""
    stmt = _apply_filters(
        select(
            Job.printer_id,
            Printer.name,
            Job.submitted_by,
            Job.page_count,
            Job.color_mode,
            Job.duplex,
        ),
        filters,
    )
    rows = (await db.execute(stmt)).all()
    return [
        CostRawRow(
            printer_id=r.printer_id,
            printer_name=r.name,
            submitted_by=r.submitted_by,
            page_count=r.page_count or 0,
            color_mode=r.color_mode,
            duplex=r.duplex,
        )
        for r in rows
    ]


async def get_raw_rows_for_export(db: AsyncSession, filters: ReportFilters):
    """Filtered job rows joined with printer name, for CSV export — one row
    per job, newest first."""
    stmt = _apply_filters(
        select(Job, Printer.name.label("printer_name")), filters
    ).order_by(Job.created_at.desc())
    return (await db.execute(stmt)).all()
