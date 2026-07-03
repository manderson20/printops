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


class LeaderboardEntryOut(BaseModel):
    key: str
    label: str
    job_count: int
    total_pages: int


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

    model_config = {"from_attributes": True}


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
