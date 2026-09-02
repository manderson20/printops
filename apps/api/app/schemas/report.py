from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class SummaryOut(BaseModel):
    total_jobs: int
    forwarded_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    total_pages: int
    color_pages: int
    mono_pages: int
    unknown_color_mode_pages: int
    duplex_pages: int
    simplex_pages: int
    unknown_duplex_pages: int
    estimated_cost_mono: float
    estimated_cost_color: float
    estimated_cost_paper: float
    estimated_cost_total: float
    sheets_of_paper: int
    duplex_sheets_saved: int
    trees_used: float
    co2_grams: float


class TimelineBucketOut(BaseModel):
    bucket_start: date
    total_pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    job_count: int


class HourlyBucketOut(BaseModel):
    interval: int
    total_pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    job_count: int
    # Tracked walk-up copies only (CopierUsageRecord) — see
    # HourlyBucket's docstring in app/reports/aggregation.py for why
    # untracked/estimated copies aren't included here.
    copy_pages: int
    copy_count: int


class LeaderboardEntryOut(BaseModel):
    key: str
    label: str
    job_count: int
    total_pages: int


class CombinedSummaryOut(BaseModel):
    print_pages: int
    copy_pages: int
    scan_pages: int
    fax_pages: int
    total_pages: int
    unmapped_copy_activity_count: int


class CombinedLeaderboardEntryOut(BaseModel):
    key: str
    label: str
    print_pages: int
    copy_pages: int
    total_pages: int
    # color/mono/duplex/simplex describe the print side only; the copy
    # side has its own colour split (copy_*) because a copy arrives as a
    # counter delta with no per-job duplex flag at all.
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    copy_color_pages: int
    copy_mono_pages: int
    scan_pages: int
    fax_pages: int
    print_cost: float
    copy_cost: float
    # print_cost + copy_cost.
    estimated_cost: float


class StaffPrinterUsageOut(BaseModel):
    """One printer's worth of one person's printing."""

    printer_id: UUID
    printer_name: str
    job_count: int
    pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    sheets: int
    toner_cost: float
    paper_cost: float
    total_cost: float


class StaffCopierUsageOut(BaseModel):
    """One copier's worth of one person's walk-up activity.

    color_pages + mono_pages can be less than `pages`: `measured_color`
    is false when the device reported a copy total with no colour split,
    in which case the unsplit remainder is priced at the mono rate and
    the UI should say so rather than implying the copies were mono."""

    device_id: UUID
    device_name: str
    pages: int
    color_pages: int
    mono_pages: int
    scan_pages: int
    fax_pages: int
    sheets: int
    toner_cost: float
    paper_cost: float
    total_cost: float
    measured_color: bool
    # True when these pages come from a whole-device meter assigned to
    # this person by an admin (MfpDevice.default_owner_email) rather than
    # from them identifying themselves at the copier.
    attributed_by_default_owner: bool


class StaffUsageOut(BaseModel):
    """Everything one person did, print and copy, in the filtered window —
    the per-person drill-down behind a Combined Leaderboard row."""

    email: str
    label: str
    print_pages: int
    copy_pages: int
    scan_pages: int
    fax_pages: int
    total_pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    job_count: int
    sheets: int
    print_cost: float
    copy_cost: float
    total_cost: float
    printers: list[StaffPrinterUsageOut]
    copiers: list[StaffCopierUsageOut]


class PeakTimesOut(BaseModel):
    by_day_of_week: dict[int, int]
    by_hour: dict[int, int]


class FunFactsOut(BaseModel):
    facts: list[str]


class ReportFormulaSettingsOut(BaseModel):
    cost_per_page_mono: float
    cost_per_page_color: float
    sheets_per_tree: float
    co2_grams_per_sheet: float
    cost_per_sheet_paper: float


class ReportFormulaSettingsUpdate(BaseModel):
    cost_per_page_mono: float | None = None
    cost_per_page_color: float | None = None
    sheets_per_tree: float | None = None
    co2_grams_per_sheet: float | None = None
    cost_per_sheet_paper: float | None = None


CartridgeColor = Literal["black", "cyan", "magenta", "yellow"]


class CartridgeIn(BaseModel):
    color: CartridgeColor
    cost: float
    yield_pages: int
    # Reference-only part number for this color slot, e.g. "TN-227C" — see
    # PrinterTonerCartridge.model's docstring (app/models/report.py).
    model: str | None = None
    # See PrinterTonerCartridge.warning_threshold_percent's docstring.
    warning_threshold_percent: int = 15


class CartridgeOut(BaseModel):
    color: CartridgeColor
    cost: float
    yield_pages: int
    model: str | None = None
    warning_threshold_percent: int

    # SNMP-detected, read-only — see PrinterTonerCartridge.detected_*'s
    # docstring (app/models/report.py). None until the first successful
    # POST /printers/{id}/toner-cartridges/detect.
    detected_description: str | None = None
    detected_high_capacity: bool | None = None
    detected_at: datetime | None = None
    # Live-polled, read-only — see PrinterTonerCartridge.current_level_percent's
    # docstring. None until the first successful detect/background poll.
    current_level_percent: int | None = None
    level_checked_at: datetime | None = None

    model_config = {"from_attributes": True}


class DetectedSupplyOut(BaseModel):
    """One raw supply row as read straight off the device — returned by
    POST /printers/{id}/toner-cartridges/detect alongside the updated
    CartridgeOut list, for supply types the probe saw but couldn't
    confidently match to a color slot (color is None), so nothing gets
    silently dropped."""

    description: str
    color: CartridgeColor | None
    high_capacity: bool | None
    level_percent: int | None = None


class DetectCartridgesResult(BaseModel):
    cartridges: list[CartridgeOut]
    unmatched: list[DetectedSupplyOut]


class FleetCartridgeOut(BaseModel):
    """One PrinterTonerCartridge row plus enough printer context to display
    and group it fleet-wide — GET /toner-cartridges, which powers the
    Settings > Toner Cartridges bulk-edit page. `id` is the actual DB
    primary key (unlike CartridgeOut, which never needed one since it's
    always scoped to a single already-known printer) — required here so a
    bulk update can target a specific row precisely across many printers."""

    id: UUID
    printer_id: UUID
    printer_name: str
    # The printer's own device manufacturer/model (Printer.manufacturer/
    # model) — distinct from `model` below, which is this cartridge's own
    # part number. Named with a printer_ prefix to avoid confusion between
    # the two, same as printer_name.
    printer_manufacturer: str | None = None
    printer_model: str | None = None
    building: str | None = None
    room: str | None = None
    color: CartridgeColor
    cost: float
    yield_pages: int
    model: str | None = None
    warning_threshold_percent: int
    current_level_percent: int | None = None

    model_config = {"from_attributes": True}


class BulkCartridgeUpdateIn(BaseModel):
    id: UUID
    cost: float
    yield_pages: int
    model: str | None = None


class DailyTonerLevelOut(BaseModel):
    """One point on the toner-level-over-time chart — see
    app/printers/toner_history.py:get_daily_toner_levels. Each color is
    independently None on a day with no reading for that color, not
    necessarily all four at once."""

    bucket_start: date
    black: int | None = None
    cyan: int | None = None
    magenta: int | None = None
    yellow: int | None = None


class CostEntryOut(BaseModel):
    key: str
    label: str
    job_count: int
    page_count: int
    toner_cost: float
    paper_cost: float
    total_cost: float


class SnapshotFiltersIn(BaseModel):
    """Same filter shape as the query-param filters used everywhere else,
    but as a request body since snapshot creation is a POST — the filters
    actually used get frozen into ReportSnapshot.filters verbatim."""

    building: str | None = None
    department: str | None = None
    printer_id: UUID | None = None
    submitted_by: str | None = None
    status: str | None = None
    color_mode: str | None = None
    duplex: bool | None = None


class SnapshotCreate(BaseModel):
    name: str
    range_start: date
    range_end: date
    filters: SnapshotFiltersIn = SnapshotFiltersIn()
    period_label: str = "period"


class SnapshotOut(BaseModel):
    id: UUID
    name: str
    range_start: date
    range_end: date
    filters: dict
    totals: dict
    fun_facts: list[str]
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- "Your Printing, Explained" (app/reports/equivalency.py) ------------


class MilestoneOut(BaseModel):
    name: str
    # The same rung named for mid-sentence use, where the full name would
    # repeat something already said — "17% of the way to Jefferson City",
    # not "...to Brookfield to Jefferson City". Equal to `name` unless the
    # rung defines its own short form.
    label: str
    # In the ladder's own unit, never a display unit — the same rung
    # reads naturally as feet on the personal view and miles on the
    # district one, so the client picks the wording.
    value: float


class MilestoneProgressOut(BaseModel):
    ladder_key: str
    unit: str
    total: float
    # Null when the total hasn't reached the first rung yet.
    passed: MilestoneOut | None = None
    # Null once the top of the ladder is passed — there is no further
    # target, which the client renders as an achievement rather than a bar.
    upcoming: MilestoneOut | None = None
    # 0.0-1.0 toward `upcoming`, measured from zero rather than from
    # `passed` — see MilestoneProgress in app/reports/equivalency.py.
    progress: float
    # False when the bar would read as broken rather than encouraging.
    show_progress: bool


class EquivalencyOut(BaseModel):
    key: str
    value: float
    unit: str
    # Set only for the three ladder-backed facts (distance, stack
    # height, weight).
    milestone: MilestoneProgressOut | None = None


class PersonalExplainedOut(BaseModel):
    """One person's own numbers, with the equivalencies at their scale."""

    period: str
    range_start: date
    range_end: date

    print_pages: int
    copy_pages: int
    total_pages: int
    job_count: int
    sheets: int

    color_pages: int
    mono_pages: int
    unknown_color_mode_pages: int
    duplex_pages: int
    simplex_pages: int
    unknown_duplex_pages: int

    largest_job_pages: int | None = None
    avg_pages_per_job: float

    # District context, as scalars only — a viewer never receives the
    # per-person totals these were computed from.
    district_median_pages: float
    district_mean_pages: float
    # Null rather than infinity when the median is 0, so the client shows
    # nothing instead of a nonsense multiple.
    times_district_median: float | None = None

    # Framed as opportunity, never as blame.
    duplex_sheets_saved: int
    additional_sheets_if_all_duplex: int

    print_cost: float
    copy_cost: float
    total_cost: float

    equivalencies: list[EquivalencyOut]
    facts: list[str]

    # Honesty flags the UI renders as footnotes rather than hiding.
    # True when any copy page is included: those come from counter deltas
    # covering a period, not from timestamped events.
    includes_period_derived_copies: bool
    # False whenever copies are included, because a counter window has no
    # hour — "busiest hour" would silently describe printing only.
    time_of_day_available: bool


class DistrictFunFactsOut(BaseModel):
    """The all-users view. Aggregates only.

    There is deliberately no field on this model that could hold a
    person, a building or a department — the anonymity rule is carried by
    the type, so it cannot be broken by forgetting to filter something
    out later. See app/routers/reports.py:report_district_fun_facts.
    """

    period: str
    range_start: date
    range_end: date

    print_pages: int
    copy_pages: int
    total_pages: int
    sheets: int

    # A count, never the people it counted.
    contributors: int
    # False when `contributors` is below the anonymity floor, in which
    # case every list below is empty and the client says there isn't
    # enough activity yet rather than showing a total two people could
    # de-anonymize between them.
    has_enough_activity: bool

    equivalencies: list[EquivalencyOut]
    facts: list[str]


class DistrictSegmentOut(BaseModel):
    """One building or department. Admin-only by construction — this
    model is reachable only from DistrictDetailOut."""

    key: str
    label: str
    people: int
    print_pages: int
    copy_pages: int
    total_pages: int
    sheets: int
    estimated_cost: float


class DistrictDetailOut(BaseModel):
    """The admin breakdown — the same totals as the all-users view, plus
    the segmentation that view must never carry."""

    period: str
    range_start: date
    range_end: date

    print_pages: int
    copy_pages: int
    total_pages: int
    sheets: int
    contributors: int

    district_median_pages: float
    district_mean_pages: float

    by_building: list[DistrictSegmentOut]
    by_department: list[DistrictSegmentOut]

    equivalencies: list[EquivalencyOut]


class MyActivityRowOut(BaseModel):
    """One line item in a person's own activity list.

    A print and a copy are not the same kind of event and this model does
    not flatten them into one. `at` is set for a print, which happened at
    an instant; `window_start`/`window_end` are set for a copy, which is
    the difference between two counter readings and therefore covers a
    period. The other side is always null — see app/reports/activity.py.
    """

    kind: str  # print | copy
    label: str
    where: str
    activity_type: str  # print | copy | scan | fax
    pages: int
    sheets: int

    at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    color_mode: str | None = None
    duplex: bool | None = None
    color_pages: int | None = None
    mono_pages: int | None = None


class MyActivityOut(BaseModel):
    period: str
    range_start: date
    range_end: date
    rows: list[MyActivityRowOut]
    # The true count before the cap, so the page can say "showing 50 of
    # 213" rather than presenting a slice as the whole history.
    total_rows: int
    # True when any copy row is present, so the page can explain why some
    # rows carry a time range instead of a time.
    includes_period_derived_copies: bool
