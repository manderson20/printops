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

from app.models.copier_usage import CopierUsageRecord
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceUser
from app.models.job import Job
from app.models.mosyle import MosyleDevice
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
class HourlyBucket:
    """One hour's worth of activity within a single day — the Live
    Dashboard's intraday view (app/routers/reports.py's /live/hourly),
    distinct from get_timeline's day/week/month buckets above. `hour` is
    just an index (0-23), not a wall-clock hour-of-day in any particular
    timezone — see get_hourly_timeline's docstring for why."""

    hour: int
    total_pages: int = 0
    color_pages: int = 0
    mono_pages: int = 0
    duplex_pages: int = 0
    simplex_pages: int = 0
    job_count: int = 0


async def get_hourly_timeline(
    db: AsyncSession, start: datetime, end: datetime
) -> list[HourlyBucket]:
    """24 (or however many actually fit between start/end) hourly
    buckets, always fully zero-filled — a bar chart needs a stable x-axis
    as the day progresses, not an array that grows hour by hour.

    Deliberately timezone-naive here: `hour` is "hours elapsed since
    start", not Job.created_at's UTC hour-of-day. This server only ever
    stores UTC timestamps, but a TV-mounted live dashboard needs to line
    up with the viewer's actual wall clock — so the caller (app/routers/
    reports.py) is expected to pass a browser-local midnight-to-midnight
    (or midnight-to-now) window as start/end, computed client-side, and
    this just buckets relative to that, whatever timezone it represents."""
    filters = ReportFilters(start=start, end=end)
    rows = await _fetch_raw_rows(db, filters)

    # .replace(tzinfo=None) before subtracting, not just for start/end here
    # but for each row's created_at below too — SQLite (used in tests, see
    # tests/test_reports_api.py) doesn't actually enforce DateTime(timezone=True)
    # the way Postgres does and can hand back naive datetimes even though
    # the column is declared aware; naive-minus-aware raises TypeError.
    # Both sides represent the same UTC instant either way, so stripping
    # tzinfo from both changes nothing about the actual elapsed-hours math.
    naive_start = start.replace(tzinfo=None)
    hour_count = max(1, int((end - start).total_seconds() // 3600))
    buckets = {h: HourlyBucket(hour=h) for h in range(hour_count)}
    for r in rows:
        naive_created_at = r.created_at.replace(tzinfo=None)
        elapsed_hours = int((naive_created_at - naive_start).total_seconds() // 3600)
        bucket = buckets.get(elapsed_hours)
        if bucket is None:
            continue  # outside the requested window — shouldn't happen, but not fatal
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
    return [buckets[h] for h in range(hour_count)]


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
    stmt = (
        _apply_filters(
            select(
                Job.printer_id,
                Printer.name,
                func.count(Job.id).label("job_count"),
                func.sum(func.coalesce(Job.page_count, 0)).label("total_pages"),
            ),
            filters,
        )
        .group_by(Job.printer_id, Printer.name)
        .order_by(func.count(Job.id).desc())
        .limit(limit)
    )
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
    stmt = (
        _apply_filters(
            select(
                Job.submitted_by,
                func.count(Job.id).label("job_count"),
                func.sum(func.coalesce(Job.page_count, 0)).label("total_pages"),
            ),
            filters,
        )
        .where(Job.submitted_by.is_not(None))
        .group_by(Job.submitted_by)
        .order_by(func.count(Job.id).desc())
        .limit(limit)
    )
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
    """One job's worth of the fields needed to price it (plus
    file_size_bytes, used by app/routers/jobs.py:list_job_usage for its
    byte totals — same row shape, just one more column, rather than a
    second near-identical query) — printer identity is included because
    toner rate is per-printer (see app/reports/formulas.py:
    compute_printer_rate), unlike the plain _RawRow above which only
    timeline/peak-times need."""

    printer_id: UUID
    printer_name: str
    submitted_by: str | None
    mac_address: str | None
    page_count: int
    color_mode: str | None
    duplex: bool | None
    file_size_bytes: int | None


async def get_cost_raw_rows(db: AsyncSession, filters: ReportFilters) -> list[CostRawRow]:
    """Feeds real per-job cost calculation (app/routers/reports.py's
    cost-breakdown endpoint, app/routers/jobs.py's usage report) — kept in
    this module rather than computing cost here directly so aggregation.py
    stays DB-query-only and app/reports/formulas.py stays a pure, DB-free
    calculation module (it already imports from here; importing back would
    be circular)."""
    stmt = _apply_filters(
        select(
            Job.printer_id,
            Printer.name,
            Job.submitted_by,
            Job.mac_address,
            Job.page_count,
            Job.color_mode,
            Job.duplex,
            Job.file_size_bytes,
        ),
        filters,
    )
    rows = (await db.execute(stmt)).all()
    return [
        CostRawRow(
            printer_id=r.printer_id,
            printer_name=r.name,
            submitted_by=r.submitted_by,
            mac_address=r.mac_address,
            page_count=r.page_count or 0,
            color_mode=r.color_mode,
            duplex=r.duplex,
            file_size_bytes=r.file_size_bytes,
        )
        for r in rows
    ]


async def get_raw_rows_for_export(db: AsyncSession, filters: ReportFilters):
    """Filtered job rows joined with printer name, for CSV export — one row
    per job, newest first."""
    stmt = _apply_filters(select(Job, Printer.name.label("printer_name")), filters).order_by(
        Job.created_at.desc()
    )
    return (await db.execute(stmt)).all()


# --- Combined print + copy reporting (Stage 1 copier accounting) ---
#
# CopierUsageRecord.staff_email is deliberately the same loose, plain-string
# join key as Job.submitted_by (see app/models/copier_usage.py) — merging
# the two here is Python dict merging by that shared string, not a SQL
# join, so neither table needs a schema change to support this.
#
# Only start/end/building/submitted_by from ReportFilters apply to the
# copier side — printer_id/status/color_mode/duplex are IPP-job-specific
# concepts with no CopierUsageRecord equivalent, and are silently ignored
# here rather than erroring (a combined report with, say, a color_mode
# filter still shows unfiltered copy totals; this is a known Stage 1
# simplification, not a bug). Date filtering uses created_at (when the
# usage was recorded in PrintOps — via import or a future direct
# connector), not occurred_at/period_start/period_end, since not every
# row has occurred_at populated (period-based rows don't) and created_at
# is the one timestamp every row always has.


def _apply_copier_filters(stmt: Select, filters: ReportFilters) -> Select:
    if filters.start is not None:
        stmt = stmt.where(CopierUsageRecord.created_at >= filters.start)
    if filters.end is not None:
        stmt = stmt.where(CopierUsageRecord.created_at < filters.end)
    if filters.building is not None:
        stmt = stmt.where(CopierUsageRecord.location_building == filters.building)
    if filters.submitted_by is not None:
        stmt = stmt.where(CopierUsageRecord.staff_email == filters.submitted_by)
    return stmt


@dataclass
class CopyTotals:
    copy_record_count: int = 0
    copy_pages: int = 0


async def get_copier_usage_totals(
    db: AsyncSession, filters: ReportFilters
) -> dict[str, CopyTotals]:
    """staff_email -> aggregated copy totals — the copier-side mirror of
    get_user_leaderboard, over CopierUsageRecord instead of Job. Excludes
    unmapped rows (staff_email is null) entirely; see
    get_unmapped_copier_activity_count for surfacing those separately
    rather than silently dropping them from the report."""
    stmt = _apply_copier_filters(
        select(
            CopierUsageRecord.staff_email,
            func.count(CopierUsageRecord.id),
            func.sum(func.coalesce(CopierUsageRecord.page_count, 0)),
        ).where(CopierUsageRecord.staff_email.is_not(None)),
        filters,
    ).group_by(CopierUsageRecord.staff_email)
    rows = (await db.execute(stmt)).all()
    return {
        email: CopyTotals(copy_record_count=count, copy_pages=pages or 0)
        for email, count, pages in rows
    }


async def get_unmapped_copier_activity_count(db: AsyncSession, filters: ReportFilters) -> int:
    """Count of CopierUsageRecord rows with no resolved staff_email in this
    filtered window — surfaced as its own callout on the combined report
    (app/routers/copier_unmapped.py is where an admin actually resolves
    these), never silently excluded from view."""
    stmt = _apply_copier_filters(
        select(func.count(CopierUsageRecord.id)).where(CopierUsageRecord.staff_email.is_(None)),
        filters,
    )
    return (await db.execute(stmt)).scalar_one() or 0


@dataclass
class CombinedSummary:
    print_pages: int = 0
    copy_pages: int = 0
    total_pages: int = 0
    unmapped_copy_activity_count: int = 0


async def get_combined_summary(db: AsyncSession, filters: ReportFilters) -> CombinedSummary:
    print_summary = await get_summary(db, filters)
    copy_totals = await get_copier_usage_totals(db, filters)
    copy_pages = sum(t.copy_pages for t in copy_totals.values())
    unmapped_count = await get_unmapped_copier_activity_count(db, filters)
    return CombinedSummary(
        print_pages=print_summary.total_pages,
        copy_pages=copy_pages,
        total_pages=print_summary.total_pages + copy_pages,
        unmapped_copy_activity_count=unmapped_count,
    )


@dataclass
class CombinedLeaderboardEntry:
    key: str  # staff email
    label: str
    print_pages: int = 0
    copy_pages: int = 0
    total_pages: int = 0
    color_pages: int = 0
    mono_pages: int = 0
    duplex_pages: int = 0
    simplex_pages: int = 0
    # Print-only — walk-up copy usage has no cost model (see
    # get_copier_usage_totals). Left at 0.0 here; the combined-leaderboard
    # router endpoint fills this in from the same cost accumulator
    # report_cost_breakdown uses (app/routers/reports.py), since that
    # depends on admin-configured formula settings this module doesn't
    # have access to.
    estimated_cost: float = 0.0


async def _get_all_user_print_totals(
    db: AsyncSession, filters: ReportFilters
) -> dict[str, CombinedLeaderboardEntry]:
    """Same underlying query as get_user_leaderboard, but with no limit —
    the combined leaderboard below needs every user's print total before
    it can rank by combined (print + copy) pages; get_user_leaderboard's
    own limit would otherwise cut someone with modest print volume but
    heavy copy volume before the copy side is even merged in, undercounting
    them in the combined ranking. Also broken down by color/duplex, same
    CASE-WHEN-via-.filter() idiom as get_summary, just grouped by user."""
    pages = func.coalesce(Job.page_count, 0)
    stmt = (
        _apply_filters(
            select(
                Job.submitted_by,
                func.sum(pages).label("total_pages"),
                func.sum(pages).filter(Job.color_mode == "color").label("color_pages"),
                func.sum(pages).filter(Job.color_mode == "monochrome").label("mono_pages"),
                func.sum(pages).filter(Job.duplex.is_(True)).label("duplex_pages"),
                func.sum(pages).filter(Job.duplex.is_(False)).label("simplex_pages"),
            ),
            filters,
        )
        .where(Job.submitted_by.is_not(None))
        .group_by(Job.submitted_by)
    )
    rows = (await db.execute(stmt)).all()
    return {
        r.submitted_by: CombinedLeaderboardEntry(
            key=r.submitted_by,
            label=r.submitted_by,
            print_pages=r.total_pages or 0,
            total_pages=r.total_pages or 0,
            color_pages=r.color_pages or 0,
            mono_pages=r.mono_pages or 0,
            duplex_pages=r.duplex_pages or 0,
            simplex_pages=r.simplex_pages or 0,
        )
        for r in rows
    }


async def resolve_display_names(db: AsyncSession, emails: set[str]) -> dict[str, str]:
    """email -> best display label: the synced Google Workspace name if
    known, else the email's local-part (before @) as a readable stand-in
    for someone not in the roster yet (e.g. before an attribution alias
    is merged) — same local-part idiom app/attribution/resolve.py uses on
    the lookup side, applied here for display instead."""
    if not emails:
        return {}
    result = await db.execute(
        select(GoogleWorkspaceUser.email, GoogleWorkspaceUser.name).where(
            GoogleWorkspaceUser.email.in_(emails)
        )
    )
    names = dict(result.all())
    return {email: names.get(email) or email.split("@", 1)[0] for email in emails}


async def resolve_device_names(db: AsyncSession, macs: set[str]) -> dict[str, str]:
    """mac_address -> best display label: the device's name from whichever
    MDM roster has it (Mosyle for Mac/iPad, Google Workspace for
    ChromeOS — both keyed by unique mac_address), falling back to the raw
    MAC string itself for a device in neither roster yet. Never hides a
    MAC outright — an admin can still act on it even unresolved, same
    philosophy as resolve_display_names' local-part fallback above."""
    if not macs:
        return {}
    mosyle_result = await db.execute(
        select(MosyleDevice.mac_address, MosyleDevice.device_name).where(
            MosyleDevice.mac_address.in_(macs)
        )
    )
    names = {mac: name for mac, name in mosyle_result.all() if name}
    remaining = macs - names.keys()
    if remaining:
        google_result = await db.execute(
            select(GoogleWorkspaceDevice.mac_address, GoogleWorkspaceDevice.device_name).where(
                GoogleWorkspaceDevice.mac_address.in_(remaining)
            )
        )
        names.update({mac: name for mac, name in google_result.all() if name})
    return {mac: names.get(mac, mac) for mac in macs}


async def get_combined_user_leaderboard(
    db: AsyncSession, filters: ReportFilters, limit: int = 10
) -> list[CombinedLeaderboardEntry]:
    merged = await _get_all_user_print_totals(db, filters)
    copy_totals = await get_copier_usage_totals(db, filters)

    for email, copy_entry in copy_totals.items():
        entry = merged.setdefault(email, CombinedLeaderboardEntry(key=email, label=email))
        entry.copy_pages = copy_entry.copy_pages
        entry.total_pages += copy_entry.copy_pages

    display_names = await resolve_display_names(db, set(merged.keys()))
    for email, entry in merged.items():
        entry.label = display_names.get(email, email)

    return sorted(merged.values(), key=lambda e: e.total_pages, reverse=True)[:limit]
