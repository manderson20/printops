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
    total_pages: int
    unmapped_copy_activity_count: int


class CombinedLeaderboardEntryOut(BaseModel):
    key: str
    label: str
    print_pages: int
    copy_pages: int
    total_pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int
    # Print-only — walk-up copy usage has no cost model.
    estimated_cost: float


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


class CartridgeOut(BaseModel):
    color: CartridgeColor
    cost: float
    yield_pages: int

    # SNMP-detected, read-only — see PrinterTonerCartridge.detected_*'s
    # docstring (app/models/report.py). None until the first successful
    # POST /printers/{id}/toner-cartridges/detect.
    detected_description: str | None = None
    detected_high_capacity: bool | None = None
    detected_at: datetime | None = None

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


class DetectCartridgesResult(BaseModel):
    cartridges: list[CartridgeOut]
    unmatched: list[DetectedSupplyOut]


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
