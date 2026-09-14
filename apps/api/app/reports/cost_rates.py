"""Loads each printer's real toner cost-per-page rate for cost
calculations (app/reports/formulas.py:job_cost) — split into its own
module rather than living in aggregation.py or formulas.py: it does a DB
query (like aggregation.py's get_cost_raw_rows) but also calls
compute_printer_rate (formulas.py), and formulas.py already imports from
aggregation.py (physical_sheets_used) — putting this here avoids that
would-be circular import while still letting both app/routers/reports.py
and app/routers/jobs.py share one implementation instead of duplicating
it."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import PrinterTonerCartridge, ReportFormulaSettings
from app.reports.formulas import FormulaValues, PrinterTonerRate, compute_printer_rate
from app.reports.rate_history import (
    load_district_rate_timeline,
    load_printer_price_timelines,
)


async def load_printer_rates(
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


@dataclass(frozen=True)
class JobRates:
    """Everything needed to price one job or copy, as it stood on its own day."""

    toner: PrinterTonerRate
    cost_per_sheet_paper: float
    # The flat rates in force then, for the environmental/summary figures that
    # are computed from aggregate page counts rather than per job.
    formulas: FormulaValues


class DatedRates:
    """Rates resolved per job date rather than once per report.

    Before rates had dates every figure was computed at today's, which is fine
    until two periods are compared: a year-on-year total would reprice last
    year at this year's toner prices and report the difference as a change in
    printing. See app/reports/rate_history.py.

    Results are memoised by (printer, day) because a report walks every job it
    covers — tens of thousands for a district year — while the number of
    distinct (printer, day) pairs is small and the number of *rate periods*
    smaller still.
    """

    def __init__(self, price_timelines, district_timeline):
        self._prices = price_timelines
        self._district = district_timeline
        self._memo: dict[tuple[UUID | None, date], JobRates] = {}

    def on(self, printer_id: UUID | None, day: date) -> JobRates:
        key = (printer_id, day)
        cached = self._memo.get(key)
        if cached is not None:
            return cached

        district = self._district.on(day)
        timeline = self._prices.get(printer_id) if printer_id is not None else None
        # No printer, or one whose cartridges nobody has entered: the flat
        # district rates, exactly as compute_printer_rate already falls back
        # for a partially configured printer.
        cartridges = timeline.on(day) if timeline is not None else []
        rates = JobRates(
            toner=compute_printer_rate(cartridges, district.formulas),
            cost_per_sheet_paper=district.cost_per_sheet_paper,
            formulas=district.formulas,
        )
        self._memo[key] = rates
        return rates


async def load_dated_rates(
    db: AsyncSession, printer_ids: set[UUID], settings: ReportFormulaSettings
) -> DatedRates:
    """The two timelines a cost report needs, loaded once."""
    return DatedRates(
        price_timelines=await load_printer_price_timelines(db, printer_ids),
        district_timeline=await load_district_rate_timeline(db, settings),
    )
