import uuid
from datetime import date

from sqlalchemy import JSON, Date
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReportFormulaSettings(Base, TimestampMixin):
    """Effectively a singleton (one row, same pattern as MosyleSettings) —
    the admin-configurable constants Print Insights uses to turn raw job
    facts (pages, color_mode, duplex) into cost/environmental estimates. Not
    stored per-job: changing these only affects future report queries, never
    rewrites historical Job rows — a saved ReportSnapshot is the only thing
    that freezes a computed total against formula drift (see ReportSnapshot
    below and app/reports/formulas.py)."""

    __tablename__ = "report_formula_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    cost_per_page_mono: Mapped[float] = mapped_column(default=0.03, server_default="0.03")
    cost_per_page_color: Mapped[float] = mapped_column(default=0.10, server_default="0.10")
    # EPA-style rough estimate: ~8,333 sheets of office paper per tree.
    sheets_per_tree: Mapped[float] = mapped_column(default=8333.0, server_default="8333.0")
    # Rough estimate: ~4.6 grams of CO2 per sheet (production + printing).
    co2_grams_per_sheet: Mapped[float] = mapped_column(default=4.6, server_default="4.6")


class ReportSnapshot(Base, TimestampMixin):
    """An admin-saved, point-in-time report — freezes `totals`/`fun_facts`
    as computed at save time so they stay stable even if
    ReportFormulaSettings or the underlying job data changes later (e.g. a
    device-override backfill re-attributing old jobs). `filters` records
    exactly what criteria produced this snapshot, for display/audit context
    only — re-running a live report with the same filters is not guaranteed
    to reproduce the frozen numbers verbatim, by design. `created_at` (from
    TimestampMixin) is when the snapshot was saved; `range_start`/`range_end`
    is the reporting period it covers."""

    __tablename__ = "report_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    name: Mapped[str]
    range_start: Mapped[date] = mapped_column(Date)
    range_end: Mapped[date] = mapped_column(Date)

    filters: Mapped[dict] = mapped_column(JSON)
    totals: Mapped[dict] = mapped_column(JSON)
    fun_facts: Mapped[list] = mapped_column(JSON)

    created_by: Mapped[str]
