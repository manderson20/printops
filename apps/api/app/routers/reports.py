import csv
import io
import logging
import statistics
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.destination import Destination
from app.models.location import Location
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.models.report import ReportFormulaSettings, ReportSnapshot
from app.models.user import User
from app.reports.activity import get_my_activity
from app.reports.aggregation import (
    CopyCostRawRow,
    CostRawRow,
    ReportFilters,
    count_contributors,
    get_combined_summary,
    get_combined_user_leaderboard,
    get_copier_activity_by_device,
    get_copy_cost_raw_rows,
    get_cost_raw_rows,
    get_hourly_timeline,
    get_largest_job_pages,
    get_pages_per_person,
    get_peak_times,
    get_printer_leaderboard,
    get_raw_rows_for_export,
    get_summary,
    get_timeline,
    get_user_leaderboard,
    local,
    resolve_device_names,
    resolve_display_names,
    resolve_ou_scoped_emails,
)
from app.reports.cost_rates import load_printer_rates
from app.reports.equivalency import (
    build_equivalencies,
    duplex_sheets_saved,
    resolve_period,
    sheets_from_totals,
    sheets_if_all_duplex,
)
from app.reports.equivalency_config import MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS
from app.reports.formulas import (
    FormulaValues,
    JobCost,
    compute_environmental_impact,
    compute_printer_rate,
    copy_cost,
    job_cost,
)
from app.reports.fun_facts import (
    duplex_opportunity_fact,
    generate_equivalency_facts,
    generate_fun_facts,
)
from app.reports.road_trip import build_route, ladder_from_destinations
from app.reports.tracked_copies import get_tracked_copy_summary
from app.reports.untracked_copies import get_untracked_copy_summary
from app.schemas.auth import UserOut
from app.schemas.report import (
    CombinedLeaderboardEntryOut,
    CombinedSummaryOut,
    CostEntryOut,
    DistrictDetailOut,
    DistrictFunFactsOut,
    DistrictSegmentOut,
    EquivalencyOut,
    FunFactsOut,
    HourlyBucketOut,
    LeaderboardEntryOut,
    MilestoneOut,
    MilestoneProgressOut,
    MyActivityOut,
    MyActivityRowOut,
    PeakTimesOut,
    PersonalExplainedOut,
    RouteOut,
    RoutePositionOut,
    RouteStopOut,
    SnapshotCreate,
    SnapshotOut,
    StaffCopierUsageOut,
    StaffPrinterUsageOut,
    StaffUsageOut,
    SummaryOut,
    TimelineBucketOut,
)
from app.schemas.untracked_copies import (
    TrackedCopyDeviceEntryOut,
    TrackedCopySummaryOut,
    UntrackedCopyPrinterEntryOut,
    UntrackedCopySummaryOut,
)
from app.server_settings.service import get_or_create_server_settings

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _range_boundary(value: str | None, tz: ZoneInfo) -> datetime | None:
    """One end of a report's range, as an instant.

    A bare date means midnight in the district's own zone: 2026-08-23 is the
    day people worked, not the UTC day that starts at 7pm the evening before.
    Anything else is parsed as an instant and used unchanged, so the intraday
    dashboard — which computes a real local midnight client-side and sends it
    with an offset — keeps working exactly as it did."""
    if value is None or not value.strip():
        return None
    text = value.strip()

    # Which form this is has to be decided before parsing, not by trying the
    # instant parser first and falling through: datetime.fromisoformat accepts
    # a bare "2026-08-23" and hands back a naive midnight, so a date would be
    # silently read as midnight UTC — five hours off, and exactly the bug this
    # function exists to prevent. A test caught it.
    if "T" in text or " " in text:
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{value!r} is not a date or a timestamp",
            ) from exc
        if moment.tzinfo is None:
            # No offset means whoever sent it was thinking in local terms;
            # the server's own clock is not the answer to that question.
            moment = moment.replace(tzinfo=tz)
        return moment.astimezone(UTC)

    try:
        day = date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{value!r} is not a date or a timestamp",
        ) from exc
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


async def _report_filters(
    start: str | None = None,
    end: str | None = None,
    building: str | None = None,
    department: str | None = None,
    printer_id: UUID | None = None,
    submitted_by: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    color_mode: str | None = None,
    duplex: bool | None = None,
    current_user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportFilters:
    """Shared query-param parsing + RBAC scoping for every report endpoint.
    A plain "viewer" only ever sees their own print history —
    `submitted_by` is force-set to their own identity (the same value
    Job.submitted_by stores for them, since UserOut.username *is* their
    attributed email for SSO logins — see app/schemas/auth.py), overriding
    whatever they passed. An "ou_viewer" instead gets `submitted_by_in`
    set to every roster email under their granted OUs — granted_ou_paths
    is re-read from the User row here, never trusted from the JWT (see
    app.deps.get_current_user's docstring), so a grant change an admin
    makes takes effect on this account's very next request, not just its
    next login. "admin" is unrestricted, as before.

    start/end accept either a date (2026-08-23) or a full instant. A date is
    the boundary of a *day in the district*, resolved here against the
    configured zone — the only place that knows it. The Insights presets used
    to send instants built from the browser's midnight, so an admin in another
    timezone, or with a wrong device clock, filtered one day's worth of jobs
    and then had them bucketed by another: part of the requested day missing,
    and what survived split across two dates on the chart. An instant is still
    accepted and used as given, which is what the live dashboard's intraday
    view sends."""
    zone = await _district_zone(db)
    start_at = _range_boundary(start, zone)
    end_at = _range_boundary(end, zone)
    submitted_by_in = None
    if current_user.role == "ou_viewer":
        result = await db.execute(
            select(User.granted_ou_paths).where(
                func.lower(User.email) == (current_user.email or "").lower()
            )
        )
        granted_ou_paths = result.scalar_one_or_none() or []
        submitted_by_in = await resolve_ou_scoped_emails(db, granted_ou_paths)
        submitted_by = None
    elif current_user.role != "admin":
        submitted_by = current_user.username
    return ReportFilters(
        start=start_at,
        end=end_at,
        building=building,
        department=department,
        printer_id=printer_id,
        submitted_by=submitted_by,
        submitted_by_in=submitted_by_in,
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


async def _compute_cost_accumulators(
    db: AsyncSession,
    filters: ReportFilters,
    cost_per_sheet_paper: float,
    fallback: FormulaValues,
) -> tuple[
    dict[str, _CostAccumulator],
    dict[str, _CostAccumulator],
    dict[str, _CostAccumulator],
    _CostAccumulator,
]:
    """Returns (by_printer, by_user, by_device, overall) computed together
    in one pass over the raw job rows, since every grouping needs the
    identical per-job cost (real per-printer toner rate + physical-sheet-
    accurate paper cost — see the module docstring in aggregation.py's
    get_cost_raw_rows for why this can't be a single SQL GROUP BY).
    by_device lets the same person's Mac and iPad (same Workspace/Mosyle
    account) show up as distinct rows, since mac_address — not
    submitted_by — is the grouping key."""
    rows = await get_cost_raw_rows(db, filters)
    printer_ids = {r.printer_id for r in rows}
    rates = await load_printer_rates(db, printer_ids, fallback)

    by_printer: dict[str, _CostAccumulator] = {}
    by_user: dict[str, _CostAccumulator] = {}
    by_device: dict[str, _CostAccumulator] = {}
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

        if row.mac_address:
            device_entry = by_device.setdefault(
                row.mac_address, _CostAccumulator(label=row.mac_address)
            )
            _accumulate(device_entry, row, cost)

        _accumulate(overall, row, cost)

    # by_printer's labels are already real printer names; by_user's (raw
    # submitted_by email so far) and by_device's (raw MAC so far) benefit
    # from the same resolve-to-a-real-name treatment the Combined
    # Leaderboard uses — see app/reports/aggregation.py.
    display_names = await resolve_display_names(db, set(by_user.keys()))
    for email, entry in by_user.items():
        entry.label = display_names.get(email, email)

    device_names = await resolve_device_names(db, set(by_device.keys()))
    for mac, entry in by_device.items():
        entry.label = device_names.get(mac, mac)

    return by_printer, by_user, by_device, overall


@dataclass
class _CopyCostAccumulator:
    """The copy-side counterpart of _CostAccumulator.

    Not the same class, because the two are not the same shape: a print
    row has one color mode and contributes to exactly one of mono/color,
    while a copy row is a counter delta carrying both at once, plus a
    remainder the device never split. That remainder is tracked in its own
    field rather than folded into mono — it is priced at the mono rate,
    but calling it mono would state something the device never said, and
    the UI needs to be able to tell the difference to label it."""

    label: str
    page_count: int = 0
    color_pages: int = 0
    mono_pages: int = 0
    unsplit_pages: int = 0
    sheets: int = 0
    toner_cost: float = 0.0
    paper_cost: float = 0.0

    @property
    def total_cost(self) -> float:
        return self.toner_cost + self.paper_cost

    @property
    def measured_color(self) -> bool:
        """False when nothing in this bucket was broken out by color, so
        every page in it is being priced at the mono rate by default."""
        return self.unsplit_pages == 0 or bool(self.color_pages or self.mono_pages)


def _accumulate_copy(entry: _CopyCostAccumulator, row: CopyCostRawRow, cost: JobCost) -> None:
    color = row.color_page_count or 0
    mono = row.monochrome_page_count or 0
    entry.page_count += row.page_count
    entry.color_pages += color
    entry.mono_pages += mono
    entry.unsplit_pages += max(row.page_count - color - mono, 0)
    entry.sheets += cost.sheets
    entry.toner_cost += cost.toner_cost
    entry.paper_cost += cost.paper_cost


async def _compute_copy_cost_accumulators(
    db: AsyncSession,
    filters: ReportFilters,
    cost_per_sheet_paper: float,
    fallback: FormulaValues,
) -> tuple[dict[str, _CopyCostAccumulator], dict[str, _CopyCostAccumulator], _CopyCostAccumulator]:
    """Returns (by_user, by_device, overall) for walk-up copying, priced at
    the same per-printer cartridge rates printing uses.

    The rate comes from the copier's *linked printer*, since that is where
    cartridges are recorded — a copier with no linked printer, or one
    whose cartridges nobody has entered, falls back to the flat admin-set
    rates exactly as an unconfigured printer does (compute_printer_rate).
    Unmapped rows (no staff_email) still count toward by_device and
    overall: the pages are real and the device really made them, it is
    only the person that is unknown."""
    rows = await get_copy_cost_raw_rows(db, filters)
    printer_ids = {r.printer_id for r in rows if r.printer_id is not None}
    rates = await load_printer_rates(db, printer_ids, fallback)
    flat_rate = compute_printer_rate([], fallback)

    by_user: dict[str, _CopyCostAccumulator] = {}
    by_device: dict[str, _CopyCostAccumulator] = {}
    overall = _CopyCostAccumulator(label="Overall")

    for row in rows:
        rate = rates.get(row.printer_id, flat_rate) if row.printer_id else flat_rate
        cost = copy_cost(
            row.page_count,
            row.color_page_count,
            row.monochrome_page_count,
            row.duplex,
            rate,
            cost_per_sheet_paper,
        )
        if row.staff_email:
            _accumulate_copy(
                by_user.setdefault(row.staff_email, _CopyCostAccumulator(label=row.staff_email)),
                row,
                cost,
            )
        _accumulate_copy(
            by_device.setdefault(str(row.device_id), _CopyCostAccumulator(label=row.device_name)),
            row,
            cost,
        )
        _accumulate_copy(overall, row, cost)

    display_names = await resolve_display_names(db, set(by_user.keys()))
    for email, entry in by_user.items():
        entry.label = display_names.get(email, email)

    return by_user, by_device, overall


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
    _, _, _, overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, formulas
    )
    return _build_summary_out(summary, environmental, overall)


@router.get("/summary", response_model=SummaryOut)
async def report_summary(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    return await _summary_out(db, filters)


def _csv_time(value, tz: ZoneInfo) -> str:
    """A timestamp for a person opening a spreadsheet.

    Written in the district's timezone with the offset kept, rather than the
    raw UTC the column stores: an export is read by someone who knows when
    they were at work, and "2026-08-23T21:56:21+00:00" is not that. The offset
    stays so the value is still unambiguous to anything that parses it."""
    if value is None:
        return ""
    return local(value, tz).isoformat(sep=" ", timespec="seconds")


async def _district_zone(db: AsyncSession) -> ZoneInfo:
    """The timezone the reports are read in — see migration 0065.

    Falls back to UTC only if the stored name has somehow stopped resolving
    (a zone dropped by a tzdata update, say). Reports five hours out are
    better than reports that 500, and the settings page still shows what is
    configured."""
    settings = await get_or_create_server_settings(db)
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "Server timezone %r could not be resolved — reading reports in UTC.",
            settings.timezone,
        )
        return ZoneInfo("UTC")


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
    buckets = await get_timeline(db, filters, granularity=granularity, tz=await _district_zone(db))
    return [TimelineBucketOut(**vars(b)) for b in buckets]


@router.get(
    "/live/hourly",
    response_model=list[HourlyBucketOut],
    dependencies=[Depends(require_role("admin"))],
)
async def report_live_hourly(start: datetime, end: datetime, db: AsyncSession = Depends(get_db)):
    """Intraday hourly buckets for the Live Dashboard (apps/web/.../live/
    page.tsx) — distinct from /timeline's day/week/month view above.
    start/end are required and computed client-side (typically the
    viewer's local midnight through now) rather than defaulted here — see
    app/reports/aggregation.py:get_hourly_timeline's docstring for why
    this endpoint has no server-side notion of "today" at all.

    Admin-only: this returns org-wide aggregate activity + (via the
    frontend's separate GET /jobs call) a recent-jobs feed, with no
    per-person scoping — it's an ops/TV-display view, not personal data,
    so it's restricted by role rather than self- or OU-scoped."""
    if end <= start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="end must be after start"
        )
    buckets = await get_hourly_timeline(db, start, end)
    return [HourlyBucketOut(**vars(b)) for b in buckets]


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


@router.get("/combined-summary", response_model=CombinedSummaryOut)
async def report_combined_summary(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    """Print + walk-up-copy totals together — see app/reports/aggregation.py's
    module docstring for which filters apply to the copier side."""
    summary = await get_combined_summary(db, filters)
    return CombinedSummaryOut(**vars(summary))


@router.get("/combined-leaderboard", response_model=list[CombinedLeaderboardEntryOut])
async def report_combined_leaderboard(
    limit: int = 10,
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    entries = await get_combined_user_leaderboard(db, filters, limit=min(limit, 50))

    # Print-only estimated cost, same real per-printer-toner-rate formula
    # /cost-breakdown uses (job_cost, app/reports/formulas.py) — computed
    # here rather than in aggregation.py's get_combined_user_leaderboard
    # since it depends on admin-configured formula settings that live at
    # this router layer, not in that lower-level aggregation module.
    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    _, cost_by_user, _, _overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, fallback
    )
    copy_cost_by_user, _, _copy_overall = await _compute_copy_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, fallback
    )
    for entry in entries:
        print_acc = cost_by_user.get(entry.key)
        copy_acc = copy_cost_by_user.get(entry.key)
        entry.print_cost = round(print_acc.total_cost, 2) if print_acc else 0.0
        entry.copy_cost = round(copy_acc.total_cost, 2) if copy_acc else 0.0
        entry.estimated_cost = round(entry.print_cost + entry.copy_cost, 2)

    return [CombinedLeaderboardEntryOut(**vars(e)) for e in entries]


def _may_report_on(current_user: UserOut, filters: ReportFilters, email: str) -> bool:
    """Whether this user may see `email`'s usage, judged from the scope
    _report_filters already computed rather than by re-deriving it — one
    place decides what a role can see, and this only reads that decision.

    admin: anyone. viewer: only the identity that was forced onto
    submitted_by, i.e. themselves. ou_viewer: only an address inside the
    roster their granted OUs resolved to. Compared case-insensitively
    because these are email addresses, which are not case-sensitive in
    the half that matters here, and the same person's address arrives
    differently cased from different rosters."""
    if current_user.role == "admin":
        return True
    target = email.lower()
    if filters.submitted_by_in is not None:
        return target in {e.lower() for e in filters.submitted_by_in}
    return filters.submitted_by is not None and filters.submitted_by.lower() == target


@router.get("/staff-usage", response_model=StaffUsageOut)
async def report_staff_usage(
    email: str = Query(..., description="staff_email / submitted_by to report on"),
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Everything one person printed and copied in the window, broken down
    by the machine that did it — the drill-down behind a Combined
    Leaderboard row.

    Scoped by overriding ReportFilters.submitted_by rather than by
    filtering the results of the fleet-wide queries: that one field is
    already the join key on both sides (Job.submitted_by and, via
    _apply_copier_filters, CopierUsageRecord.staff_email), so the same
    filters produce a consistently-scoped answer from print and copy
    without either side needing a special case.

    That override is exactly why this endpoint has to re-check permission
    itself. Every other report inherits its scoping from _report_filters,
    which force-sets submitted_by for a viewer and submitted_by_in for an
    ou_viewer — overwriting submitted_by with a caller-supplied address
    would hand any logged-in user anyone else's usage and cost. So the
    requested address is checked against the scope _report_filters
    computed *before* it is applied, and a non-admin asking about someone
    they can't see gets a 403."""
    if not _may_report_on(current_user, filters, email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this person's usage",
        )
    # Scoped to exactly one person now, so the OU-wide list is redundant —
    # and leaving it set would widen the copy side back out, since
    # _apply_copier_filters only understands submitted_by.
    filters = replace(filters, submitted_by=email, submitted_by_in=None)

    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    cost_per_sheet = formula_settings.cost_per_sheet_paper

    # --- print side, grouped per printer for this one person
    rows = await get_cost_raw_rows(db, filters)
    rates = await load_printer_rates(db, {r.printer_id for r in rows}, fallback)
    printers: dict[UUID, StaffPrinterUsageOut] = {}
    for row in rows:
        cost = job_cost(
            row.page_count, row.color_mode, row.duplex, rates[row.printer_id], cost_per_sheet
        )
        entry = printers.setdefault(
            row.printer_id,
            StaffPrinterUsageOut(
                printer_id=row.printer_id,
                printer_name=row.printer_name,
                job_count=0,
                pages=0,
                color_pages=0,
                mono_pages=0,
                duplex_pages=0,
                simplex_pages=0,
                sheets=0,
                toner_cost=0.0,
                paper_cost=0.0,
                total_cost=0.0,
            ),
        )
        entry.job_count += 1
        entry.pages += row.page_count
        if row.color_mode == "color":
            entry.color_pages += row.page_count
        elif row.color_mode == "monochrome":
            entry.mono_pages += row.page_count
        if row.duplex is True:
            entry.duplex_pages += row.page_count
        elif row.duplex is False:
            entry.simplex_pages += row.page_count
        entry.sheets += cost.sheets
        entry.toner_cost += cost.toner_cost
        entry.paper_cost += cost.paper_cost
        entry.total_cost += cost.total_cost

    for entry in printers.values():
        entry.toner_cost = round(entry.toner_cost, 2)
        entry.paper_cost = round(entry.paper_cost, 2)
        entry.total_cost = round(entry.total_cost, 2)

    # --- copy side, per device, plus the scan/fax that belongs beside it
    _by_user, copy_by_device, copy_overall = await _compute_copy_cost_accumulators(
        db, filters, cost_per_sheet, fallback
    )
    activity = {str(a.device_id): a for a in await get_copier_activity_by_device(db, filters)}

    copiers: list[StaffCopierUsageOut] = []
    # Devices are keyed off the activity query, not the cost one, so a
    # copier where this person only scanned still appears — zero cost is a
    # fact about the device, not a reason to hide that they used it.
    for device_key, act in activity.items():
        acc = copy_by_device.get(device_key)
        copiers.append(
            StaffCopierUsageOut(
                device_id=act.device_id,
                device_name=act.device_name,
                pages=act.copy_pages,
                color_pages=acc.color_pages if acc else 0,
                mono_pages=acc.mono_pages if acc else 0,
                scan_pages=act.scan_pages,
                fax_pages=act.fax_pages,
                sheets=acc.sheets if acc else 0,
                toner_cost=round(acc.toner_cost, 2) if acc else 0.0,
                paper_cost=round(acc.paper_cost, 2) if acc else 0.0,
                total_cost=round(acc.total_cost, 2) if acc else 0.0,
                measured_color=acc.measured_color if acc else True,
                attributed_by_default_owner=act.from_default_owner,
            )
        )
    copiers.sort(key=lambda c: c.pages, reverse=True)

    print_summary = await get_summary(db, filters)
    print_total_cost = round(sum(p.total_cost for p in printers.values()), 2)
    copy_total_cost = round(copy_overall.total_cost, 2)
    display_names = await resolve_display_names(db, {email})

    return StaffUsageOut(
        email=email,
        label=display_names.get(email, email),
        print_pages=print_summary.total_pages,
        copy_pages=copy_overall.page_count,
        scan_pages=sum(c.scan_pages for c in copiers),
        fax_pages=sum(c.fax_pages for c in copiers),
        total_pages=print_summary.total_pages + copy_overall.page_count,
        color_pages=print_summary.color_pages,
        mono_pages=print_summary.mono_pages,
        duplex_pages=print_summary.duplex_pages,
        simplex_pages=print_summary.simplex_pages,
        job_count=print_summary.total_jobs,
        sheets=sum(p.sheets for p in printers.values()) + copy_overall.sheets,
        print_cost=print_total_cost,
        copy_cost=copy_total_cost,
        total_cost=round(print_total_cost + copy_total_cost, 2),
        printers=sorted(printers.values(), key=lambda p: p.total_cost, reverse=True),
        copiers=copiers,
    )


@router.get("/cost-breakdown", response_model=list[CostEntryOut])
async def report_cost_breakdown(
    group_by: str = "printer",
    filters: ReportFilters = Depends(_report_filters),
    db: AsyncSession = Depends(get_db),
):
    """Real per-printer/per-job cost, grouped by printer, user, or device —
    the "cost by user"/"cost by device" report. Supersedes /leaderboard's
    job_count/total_pages for display purposes (this returns both, plus
    cost), but /leaderboard stays for callers that only need the lighter
    query. "device" groups by mac_address rather than submitted_by, so the
    same person's Mac and iPad (same Workspace/Mosyle account) show up as
    distinct rows — see app/reports/aggregation.py:resolve_device_names."""
    if group_by not in ("printer", "user", "device"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group_by must be 'printer', 'user', or 'device'",
        )
    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    by_printer, by_user, by_device, _overall = await _compute_cost_accumulators(
        db, filters, formula_settings.cost_per_sheet_paper, fallback
    )
    buckets = {"printer": by_printer, "user": by_user, "device": by_device}[group_by]
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
    peak = await get_peak_times(db, filters, tz=await _district_zone(db))
    return PeakTimesOut(by_day_of_week=peak.by_day_of_week, by_hour=peak.by_hour)


@router.get("/tracked-copies", response_model=TrackedCopySummaryOut)
async def report_tracked_copies(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    """Walk-up copier activity PrintOps can attribute to a named person,
    read back from per-account counters — the counterpart to
    /untracked-copies, and meant to be read alongside it rather than on its
    own (see app/reports/tracked_copies.py's module docstring)."""
    summary = await get_tracked_copy_summary(db, filters)
    return TrackedCopySummaryOut(
        copy_pages=summary.copy_pages,
        scan_pages=summary.scan_pages,
        fax_pages=summary.fax_pages,
        people=summary.people,
        unattributed_pages=summary.unattributed_pages,
        devices_reporting=summary.devices_reporting,
        devices=[
            TrackedCopyDeviceEntryOut(
                device_id=str(d.device_id),
                device_name=d.device_name,
                building=d.building,
                copy_pages=d.copy_pages,
                scan_pages=d.scan_pages,
                fax_pages=d.fax_pages,
                people=d.people,
                unattributed_pages=d.unattributed_pages,
            )
            for d in summary.devices
        ],
    )


@router.get("/untracked-copies", response_model=UntrackedCopySummaryOut)
async def report_untracked_copies(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    """Estimated walk-up copy activity PrintOps otherwise has no
    visibility into — see app/reports/untracked_copies.py's module
    docstring for how measured vs. estimated is decided, and why it never
    reaches back before the org-wide setting was enabled."""
    summary = await get_untracked_copy_summary(db, filters)
    return UntrackedCopySummaryOut(
        measured_copies=summary.measured_copies,
        estimated_untracked=summary.estimated_untracked,
        tracking_since=summary.tracking_since,
        printers=[
            UntrackedCopyPrinterEntryOut(
                printer_id=p.printer_id,
                printer_name=p.printer_name,
                measured_copies=p.measured_copies,
                estimated_untracked=p.estimated_untracked,
            )
            for p in summary.printers
        ],
    )


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
    zone = await _district_zone(db)
    timeline = await get_timeline(db, filters, granularity="day", tz=zone)
    peak_times = await get_peak_times(db, filters, tz=zone)
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


# --- "Your Printing, Explained" ----------------------------------------


async def _explained_window(db: AsyncSession, period: str) -> tuple[date, date, ReportFilters]:
    """Resolve a named period into the filters every explained view uses.

    Resolved against the district's configured zone rather than the
    caller's clock, for the reason _report_filters gives at length: a
    browser in another timezone would otherwise ask for one day's worth
    of activity and have it bucketed as another. `resolve_period` returns
    an inclusive end date and _apply_filters compares `< end`, so the
    range is closed by advancing a day.
    """
    try:
        zone = await _district_zone(db)
        start_date, end_date = resolve_period(period, datetime.now(zone).date())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return (
        start_date,
        end_date,
        ReportFilters(
            start=datetime.combine(start_date, time.min, tzinfo=zone),
            end=datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone),
            # Forwarded jobs only, for every explained view rather than
            # only the activity list that used to set it alone. A job
            # that never reached a printer is a queue question, not a
            # usage one, and counting it here made the header disagree
            # with the list beneath it: get_summary counts rows of every
            # status, so a cancelled job added 1 to job_count while
            # get_print_rows correctly refused to show it. Production
            # currently has 180 cancelled jobs carrying 0 pages, so the
            # pages agreed and only the counts drifted — which is exactly
            # the kind of disagreement nobody notices until they count
            # the rows by hand.
            #
            # Copies are unaffected: _apply_copier_filters has no status
            # to apply, a counter delta having no such thing.
            status="forwarded",
        ),
    )


def _milestone_out(milestone) -> MilestoneProgressOut | None:
    if milestone is None:
        return None
    return MilestoneProgressOut(
        ladder_key=milestone.ladder_key,
        unit=milestone.unit,
        total=round(milestone.total, 2),
        passed=(
            None
            if milestone.passed is None
            else MilestoneOut(
                name=milestone.passed.name,
                label=milestone.passed.label,
                value=milestone.passed.value,
            )
        ),
        upcoming=(
            None
            if milestone.upcoming is None
            else MilestoneOut(
                name=milestone.upcoming.name,
                label=milestone.upcoming.label,
                value=milestone.upcoming.value,
            )
        ),
        progress=round(milestone.progress, 4),
        show_progress=milestone.show_progress,
    )


def _equivalencies_out(equivalencies) -> list[EquivalencyOut]:
    return [
        EquivalencyOut(
            key=e.key,
            value=round(e.value, 2),
            unit=e.unit,
            milestone=_milestone_out(e.milestone),
        )
        for e in equivalencies
    ]


async def _road_trip(db: AsyncSession) -> tuple[list[Destination], Location | None]:
    """The district's configured road trip, or as much of it as exists.

    One query each, on tables with a handful of rows apiece — this runs
    once per district-page load, not per fact. Returns the destinations
    unfiltered: `ladder_from_destinations` decides what counts as a rung
    and `build_route` decides what can be a pin, and those are different
    questions (a rung needs a distance, a pin needs coordinates).
    """
    destinations = list((await db.execute(select(Destination))).scalars())
    home = (
        await db.execute(select(Location).where(Location.is_home.is_(True)))
    ).scalar_one_or_none()
    return destinations, home


def _route_out(route) -> RouteOut | None:
    if route is None:
        return None
    return RouteOut(
        home_name=route.home_name,
        home_latitude=route.home_latitude,
        home_longitude=route.home_longitude,
        miles_travelled=round(route.miles_travelled, 1),
        stops=[
            RouteStopOut(
                name=stop.name,
                label=stop.label,
                position=stop.position,
                miles=stop.miles,
                leg_miles=stop.leg_miles,
                latitude=stop.latitude,
                longitude=stop.longitude,
                reached=stop.reached,
                geometry=stop.geometry,
                is_target=stop.is_target,
            )
            for stop in route.stops
        ],
        position=(
            RoutePositionOut(
                latitude=route.position.latitude,
                longitude=route.position.longitude,
                from_label=route.position.from_label,
                to_label=route.position.to_label,
            )
            if route.position is not None
            else None
        ),
    )


def _sheets_for(summary, copy_pages: int) -> int:
    return sheets_from_totals(
        duplex_pages=summary.duplex_pages,
        simplex_pages=summary.simplex_pages,
        unknown_duplex_pages=summary.unknown_duplex_pages,
        copy_pages=copy_pages,
    )


@router.get("/explained/me", response_model=PersonalExplainedOut)
async def report_explained_me(
    period: str = Query("year", description="week | month | semester | year"),
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """One person's own printing and copying, with equivalencies at their
    scale.

    Always scoped to the caller, including for an admin — this is the
    "your printing" page, so an admin asking for it wants their own
    numbers, not the district's. That is why it builds its filters
    directly instead of depending on _report_filters, which would leave
    an admin unscoped and hand them the whole district under a heading
    saying "you".

    The district median it compares against is computed from an unscoped
    window, so the caller does get two numbers derived from everybody's
    activity — and those are withheld below the same contributor floor
    /explained/district enforces.

    That floor is the whole guard. Having no identities attached is not
    sufficient and this docstring used to claim it was: get_pages_per_person
    returning a bare list of integers protects against naming someone,
    not against a list short enough that a statistic over it *is* one
    person's number. Anonymity is a property of the population size, not
    of the field names in the response.
    """
    start_date, end_date, filters = await _explained_window(db, period)
    filters = replace(filters, submitted_by=current_user.username)

    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    cost_per_sheet = formula_settings.cost_per_sheet_paper

    summary = await get_summary(db, filters)
    *_, print_overall = await _compute_cost_accumulators(db, filters, cost_per_sheet, fallback)
    *_, copy_overall = await _compute_copy_cost_accumulators(db, filters, cost_per_sheet, fallback)
    largest_job = await get_largest_job_pages(db, filters)

    copy_pages = copy_overall.page_count
    total_pages = summary.total_pages + copy_pages
    sheets = _sheets_for(summary, copy_pages)

    # Print-only, both of them: nobody chooses a duplex setting at the
    # glass in a way PrintOps can see, so copies belong in neither the
    # saving already made nor the one still available.
    saved = duplex_sheets_saved(summary.duplex_pages)
    print_sheets = _sheets_for(summary, 0)
    all_duplex_sheets = sheets_if_all_duplex(
        duplex_pages=summary.duplex_pages,
        simplex_pages=summary.simplex_pages,
        unknown_duplex_pages=summary.unknown_duplex_pages,
    )
    additional = max(print_sheets - all_duplex_sheets, 0)

    # The same floor /explained/district enforces, for the same reason
    # and against the same disclosure. This endpoint is scoped to its
    # caller in every other respect, which made it look exempt — but the
    # median and mean below are computed over everybody, so they are a
    # district disclosure sitting on a personal page, and a guard that
    # protects one address and not the other protects neither.
    #
    # The count is len(per_person) rather than a second count_contributors
    # query: per_person *is* the population the statistics are computed
    # from, so guarding on its length cannot drift from what it guards.
    # (The two agree — get_pages_per_person and count_contributors take
    # the same union over the same activity_type == "copy" restriction.)
    #
    # Worked example from this district's own data: the week of
    # 2026-06-29 had 3 contributors. At n=3 the median is one person's
    # exact page count. At n=2 including the caller, the caller subtracts
    # their own total from the mean and reads the other person's.
    per_person = await get_pages_per_person(db, replace(filters, submitted_by=None))
    has_district_comparison = len(per_person) >= MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS
    median = statistics.median(per_person) if has_district_comparison else None
    mean = statistics.fmean(per_person) if has_district_comparison else None

    # The same ladder the district page uses: "you're 17% of the way to
    # Jefferson City" and "we're 40% of the way to Jefferson City" have to
    # be measured against the same Jefferson City, or the two pages
    # quietly disagree about where it is.
    destinations, _ = await _road_trip(db)
    equivalencies = build_equivalencies(
        total_pages, sheets, distance_ladder=ladder_from_destinations(destinations)
    )
    facts = generate_equivalency_facts(equivalencies, collective=False)
    opportunity = duplex_opportunity_fact(additional, saved)
    if opportunity is not None:
        facts.append(opportunity)

    return PersonalExplainedOut(
        period=period,
        range_start=start_date,
        range_end=end_date,
        print_pages=summary.total_pages,
        copy_pages=copy_pages,
        total_pages=total_pages,
        job_count=summary.total_jobs,
        sheets=sheets,
        color_pages=summary.color_pages,
        mono_pages=summary.mono_pages,
        unknown_color_mode_pages=summary.unknown_color_mode_pages,
        duplex_pages=summary.duplex_pages,
        simplex_pages=summary.simplex_pages,
        unknown_duplex_pages=summary.unknown_duplex_pages,
        largest_job_pages=largest_job,
        avg_pages_per_job=(
            round(summary.total_pages / summary.total_jobs, 1) if summary.total_jobs else 0.0
        ),
        district_median_pages=round(median, 1) if median is not None else None,
        district_mean_pages=round(mean, 1) if mean is not None else None,
        times_district_median=round(total_pages / median, 1) if median else None,
        has_district_comparison=has_district_comparison,
        duplex_sheets_saved=saved,
        additional_sheets_if_all_duplex=additional,
        print_cost=round(print_overall.total_cost, 2),
        copy_cost=round(copy_overall.total_cost, 2),
        total_cost=round(print_overall.total_cost + copy_overall.total_cost, 2),
        equivalencies=_equivalencies_out(equivalencies),
        facts=facts,
        includes_period_derived_copies=copy_pages > 0,
        time_of_day_available=copy_pages == 0,
    )


@router.get("/explained/me/activity", response_model=MyActivityOut)
async def report_my_activity(
    period: str = Query("year", description="week | month | semester | year"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """A person's own printing and copying as line items.

    Scoped to the caller the same way /explained/me is, and for the same
    reason: this is "your activity", so an admin asking for it wants
    their own rows rather than the district's. It builds its filters
    directly instead of depending on _report_filters, which leaves an
    admin unscoped.

    Copy rows carry a window rather than a timestamp, because that is
    what the hardware produces — see app/reports/activity.py's module
    docstring, and docs/copier-capture-konica.md S3.6 for the firmware
    test showing per-job copy data is not obtainable at all.
    """
    start_date, end_date, filters = await _explained_window(db, period)
    filters = replace(filters, submitted_by=current_user.username)

    rows, total = await get_my_activity(db, filters, limit=limit)
    return MyActivityOut(
        period=period,
        range_start=start_date,
        range_end=end_date,
        rows=[MyActivityRowOut(**vars(row)) for row in rows],
        total_rows=total,
        # Period-derived, not merely a copy: a row carrying occurred_at
        # is an instant and needs no footnote explaining a window it
        # doesn't have. window_start is the property being described, so
        # it is the property tested.
        includes_period_derived_copies=any(row.window_start is not None for row in rows),
    )


@router.get("/explained/district", response_model=DistrictFunFactsOut)
async def report_district_fun_facts(
    period: str = Query("year", description="week | month | semester | year"),
    db: AsyncSession = Depends(get_db),
):
    """District fun facts, visible to every signed-in user, fully
    anonymous.

    This endpoint deliberately does **not** depend on _report_filters.
    Every other report does, and must: that dependency is what forces a
    viewer's own identity onto submitted_by and an ou_viewer's roster
    onto submitted_by_in. Here the whole point is a district-wide total,
    so the filters are built from the period alone and no identity is
    ever attached to them. Taking _report_filters and then widening it
    back out would be the same result reached by undoing a safety
    measure, which is a much easier thing to get wrong later.

    Nothing per-person is fetched at any point. There is no leaderboard
    call, no display-name resolution, and DistrictFunFactsOut has no
    field that could carry a person, a building or a department even if
    one were computed by accident.

    Below MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS distinct contributors the
    totals are withheld rather than merely unlabelled. A page count
    shared between two people is not anonymous just because neither is
    named — either of them can subtract their own and read the other's.
    """
    start_date, end_date, filters = await _explained_window(db, period)

    contributors = await count_contributors(db, filters)
    if contributors < MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS:
        return DistrictFunFactsOut(
            period=period,
            range_start=start_date,
            range_end=end_date,
            print_pages=0,
            copy_pages=0,
            total_pages=0,
            sheets=0,
            contributors=contributors,
            has_enough_activity=False,
            equivalencies=[],
            facts=[],
        )

    summary = await get_summary(db, filters)
    combined = await get_combined_summary(db, filters)
    sheets = _sheets_for(summary, combined.copy_pages)

    destinations, home = await _road_trip(db)
    equivalencies = build_equivalencies(
        combined.total_pages, sheets, distance_ladder=ladder_from_destinations(destinations)
    )
    distance = next((e for e in equivalencies if e.key == "distance"), None)
    # No distance equivalency means the total was too small to be worth a
    # sentence, and a map of a journey nobody has started is worse than no
    # map — build_route is given zero rather than a guess.
    route = build_route(home, destinations, distance.value if distance else 0.0)
    return DistrictFunFactsOut(
        period=period,
        range_start=start_date,
        range_end=end_date,
        print_pages=combined.print_pages,
        copy_pages=combined.copy_pages,
        total_pages=combined.total_pages,
        sheets=sheets,
        contributors=contributors,
        has_enough_activity=True,
        equivalencies=_equivalencies_out(equivalencies),
        facts=generate_equivalency_facts(equivalencies, collective=True),
        route=_route_out(route),
    )


async def _segment_totals(
    db: AsyncSession,
    filters: ReportFilters,
    label: str,
    cost_per_sheet: float,
    fallback: FormulaValues,
) -> DistrictSegmentOut:
    """One building's or department's row. Deliberately built by
    re-running the same aggregation the district total uses with one
    extra filter, rather than by a GROUP BY of its own: it costs a few
    queries per segment on an admin-only page, and it guarantees a
    segment can never disagree with the total it is a part of."""
    summary = await get_summary(db, filters)
    combined = await get_combined_summary(db, filters)
    *_, print_overall = await _compute_cost_accumulators(db, filters, cost_per_sheet, fallback)
    *_, copy_overall = await _compute_copy_cost_accumulators(db, filters, cost_per_sheet, fallback)
    return DistrictSegmentOut(
        key=label,
        label=label,
        people=await count_contributors(db, filters),
        print_pages=combined.print_pages,
        copy_pages=combined.copy_pages,
        total_pages=combined.total_pages,
        sheets=_sheets_for(summary, combined.copy_pages),
        estimated_cost=round(print_overall.total_cost + copy_overall.total_cost, 2),
    )


@router.get(
    "/explained/district/detail",
    response_model=DistrictDetailOut,
    dependencies=[Depends(require_role("admin"))],
)
async def report_district_detail(
    period: str = Query("year", description="week | month | semester | year"),
    db: AsyncSession = Depends(get_db),
):
    """The admin breakdown — the same district totals, segmented.

    Admin-only server-side, not merely hidden from the nav: this is the
    one of the three views that returns data attributable to a group
    small enough to be a person, and a building with one secretary in it
    is a name to anyone who works there.

    Buildings are collected from both printers and copiers, since a
    building can have one without the other. Records with no building
    set are grouped under an explicit "Unassigned" row rather than
    dropped — 41 of 54 printers currently have a blank building, and a
    breakdown that silently omitted most of the district would be worse
    than one that shows the gap.
    """
    start_date, end_date, filters = await _explained_window(db, period)

    formula_settings = await _get_or_create_formula_settings(db)
    fallback = _formula_values(formula_settings)
    cost_per_sheet = formula_settings.cost_per_sheet_paper

    summary = await get_summary(db, filters)
    combined = await get_combined_summary(db, filters)
    per_person = await get_pages_per_person(db, filters)

    printer_buildings = (
        await db.execute(
            select(Printer.building).where(Printer.building.is_not(None), Printer.building != "")
        )
    ).scalars()
    device_buildings = (
        await db.execute(
            select(MfpDevice.building).where(
                MfpDevice.building.is_not(None), MfpDevice.building != ""
            )
        )
    ).scalars()
    buildings = sorted(set(printer_buildings) | set(device_buildings))

    departments = sorted(
        set(
            (
                await db.execute(
                    select(Printer.department).where(
                        Printer.department.is_not(None), Printer.department != ""
                    )
                )
            ).scalars()
        )
    )

    by_building = [
        await _segment_totals(db, replace(filters, building=b), b, cost_per_sheet, fallback)
        for b in buildings
    ]
    by_department = [
        await _segment_totals(db, replace(filters, department=d), d, cost_per_sheet, fallback)
        for d in departments
    ]
    # A building that exists but saw no activity in this window is noise
    # on a breakdown; a building with activity and no name is the point.
    by_building = [s for s in by_building if s.total_pages > 0]
    by_department = [s for s in by_department if s.total_pages > 0]

    # What the named segments don't account for, shown rather than
    # dropped — currently most of the district, because 41 of 54 printers
    # have no building set. Computed by subtracting the segments from the
    # whole rather than by querying "building is null", so the row is by
    # construction whatever the named rows are missing and the column
    # totals always reconcile. That construction is why the same helper
    # serves both breakdowns below without knowing which is which.
    #
    # `people` is the one field that cannot be derived this way and is
    # left at zero: the same person prints in more than one building, so
    # contributor counts overlap and subtracting them would invent a
    # number. The client shows a dash.
    *_, district_print_cost = await _compute_cost_accumulators(
        db, filters, cost_per_sheet, fallback
    )
    *_, district_copy_cost = await _compute_copy_cost_accumulators(
        db, filters, cost_per_sheet, fallback
    )
    district_sheets = _sheets_for(summary, combined.copy_pages)
    district_cost = district_print_cost.total_cost + district_copy_cost.total_cost

    def _unassigned(segments: list[DistrictSegmentOut]) -> DistrictSegmentOut:
        return DistrictSegmentOut(
            key="__unassigned__",
            label="Unassigned",
            people=0,
            print_pages=combined.print_pages - sum(s.print_pages for s in segments),
            copy_pages=combined.copy_pages - sum(s.copy_pages for s in segments),
            total_pages=combined.total_pages - sum(s.total_pages for s in segments),
            sheets=district_sheets - sum(s.sheets for s in segments),
            estimated_cost=round(district_cost - sum(s.estimated_cost for s in segments), 2),
        )

    # Both breakdowns get the remainder, not just buildings. The
    # department table had none and silently dropped everything it could
    # not name: the 5 printers with no department set, plus every copy
    # from a copier with no linked Printer to take a department from
    # (_apply_copier_filters inner-joins Printer for a department filter,
    # by design). A table the page introduces as a breakdown of the
    # district total has to add up to the district total, or say why not.
    unassigned_building = _unassigned(by_building)
    if unassigned_building.total_pages > 0:
        by_building.append(unassigned_building)
    unassigned_department = _unassigned(by_department)
    if unassigned_department.total_pages > 0:
        by_department.append(unassigned_department)

    detail_destinations, _ = await _road_trip(db)
    equivalencies = build_equivalencies(
        combined.total_pages,
        district_sheets,
        distance_ladder=ladder_from_destinations(detail_destinations),
    )
    return DistrictDetailOut(
        period=period,
        range_start=start_date,
        range_end=end_date,
        print_pages=combined.print_pages,
        copy_pages=combined.copy_pages,
        total_pages=combined.total_pages,
        sheets=district_sheets,
        contributors=await count_contributors(db, filters),
        district_median_pages=round(statistics.median(per_person), 1) if per_person else 0.0,
        district_mean_pages=round(statistics.fmean(per_person), 1) if per_person else 0.0,
        by_building=sorted(by_building, key=lambda s: s.total_pages, reverse=True),
        by_department=sorted(by_department, key=lambda s: s.total_pages, reverse=True),
        equivalencies=_equivalencies_out(equivalencies),
    )


@router.get("/export.csv")
async def export_csv(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    rows = await get_raw_rows_for_export(db, filters)
    zone = await _district_zone(db)
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
                _csv_time(job.created_at, zone),
                _csv_time(job.completed_at, zone),
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=print-insights-export.csv"},
    )


@router.get("/export-combined.csv")
async def export_combined_csv(
    filters: ReportFilters = Depends(_report_filters), db: AsyncSession = Depends(get_db)
):
    """Per-user combined print+copy summary — a sibling to /export.csv
    rather than a branch of it, since the column set genuinely differs
    (this is one row per staff member, not one row per job)."""
    entries = await get_combined_user_leaderboard(db, filters, limit=10_000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["staff_email", "printed_pages", "copied_pages", "total_pages"])
    for entry in entries:
        writer.writerow([entry.key, entry.print_pages, entry.copy_pages, entry.total_pages])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=combined-print-copy-export.csv"},
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

    zone = await _district_zone(db)
    summary = await get_summary(db, filters)
    timeline = await get_timeline(db, filters, granularity="day", tz=zone)
    peak_times = await get_peak_times(db, filters, tz=zone)
    printer_leaderboard = await get_printer_leaderboard(db, filters)
    formula_settings = await _get_or_create_formula_settings(db)
    formulas = _formula_values(formula_settings)
    environmental = compute_environmental_impact(summary, formulas)
    _, _, _, cost_overall = await _compute_cost_accumulators(
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
