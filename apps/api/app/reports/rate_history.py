"""Which cost rates were in force on a given date.

Every cost PrintOps reports is a rate multiplied by a count, and until now the
rate was always today's. That is harmless while a report only looks at the
current period, and stops being harmless the moment two periods are compared: a
year-on-year figure would reprice last year at this year's toner prices and
report the difference as a change in printing. The numbers would look plausible
and be wrong, which is the worst shape for a reporting bug.

A rate therefore has a timeline: the value in force now, on the live row, and
the closed periods behind it in a history table. This module is the only place
those two are read together, so "which price applied on 3 March" has exactly
one answer everywhere it is asked.

Dates, not timestamps. A price change is an administrative fact about a day —
somebody signed a supply contract — not an instant, and pretending to know the
minute would invite a job printed at 09:00 to be priced differently from one at
17:00 on the same day for no reason anyone could explain.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import (
    DistrictCostRateHistory,
    PrinterTonerCartridge,
    PrinterTonerPriceHistory,
    ReportFormulaSettings,
)
from app.reports.formulas import FormulaValues


@dataclass(frozen=True)
class PricedCartridge:
    """One slot's price during one period — shaped like the CartridgeLike that
    compute_printer_rate and ChannelRates already take, so a historical price
    goes through the identical calculation a current one does."""

    color: str
    cost: float
    yield_pages: int


@dataclass
class _Timeline:
    """Periods in ascending order of start date, and what applied in each.

    Held as two parallel lists so a lookup is a binary search rather than a
    scan: a cost report walks every job it covers, and a district year is tens
    of thousands of them.
    """

    starts: list[date]
    values: list

    def on(self, day: date):
        """What was in force on `day`.

        Never None: the earliest period is open-ended backwards, so a job older
        than every recorded price is priced at the oldest one rather than at
        nothing. A missing rate would be a zero cost, and a zero cost is a
        claim — that the printing was free — where the truth is only that
        nobody wrote the price down.
        """
        index = bisect_right(self.starts, day) - 1
        return self.values[max(index, 0)]


def _timeline(periods: list[tuple[date, object]]) -> _Timeline:
    ordered = sorted(periods, key=lambda p: p[0])
    return _Timeline(starts=[start for start, _ in ordered], values=[v for _, v in ordered])


async def load_printer_price_timelines(
    db: AsyncSession, printer_ids: set[UUID]
) -> dict[UUID, _Timeline]:
    """Each printer's cartridge prices over time.

    A period's value is the full set of slots priced at that moment, not one
    slot, because that is what a per-page rate is computed from: mono prices
    off black alone and colour off all four summed, so changing the cyan price
    changes the colour rate. Rebuilding the whole set per boundary date keeps
    compute_printer_rate taking exactly what it always took.
    """
    if not printer_ids:
        return {}

    current = (
        (
            await db.execute(
                select(PrinterTonerCartridge).where(
                    PrinterTonerCartridge.printer_id.in_(printer_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    past = (
        (
            await db.execute(
                select(PrinterTonerPriceHistory).where(
                    PrinterTonerPriceHistory.printer_id.in_(printer_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    timelines: dict[UUID, _Timeline] = {}
    for printer_id in printer_ids:
        slots = [row for row in current if row.printer_id == printer_id]
        history = [row for row in past if row.printer_id == printer_id]

        # Every date on which anything about this printer's pricing changed.
        # The set is per printer rather than per slot because a rate reads all
        # four slots at once, so a cyan change on a date black did not change
        # still starts a new period for the printer.
        boundaries = sorted(
            {row.priced_from for row in slots} | {row.effective_from for row in history}
        )
        if not boundaries:
            timelines[printer_id] = _timeline([(date(1970, 1, 1), [])])
            continue

        periods = []
        for start in boundaries:
            priced: list[PricedCartridge] = []
            for slot in slots:
                if slot.priced_from <= start:
                    priced.append(
                        PricedCartridge(
                            color=slot.color, cost=slot.cost, yield_pages=slot.yield_pages
                        )
                    )
                    continue
                # The current price had not started yet, so find what this slot
                # cost then. A slot with no history that far back contributes
                # nothing, and compute_printer_rate falls back to the flat rate
                # for it — the same thing it does for a slot never configured.
                was = [
                    row
                    for row in history
                    if row.color == slot.color and row.effective_from <= start < row.effective_to
                ]
                if was:
                    priced.append(
                        PricedCartridge(
                            color=slot.color, cost=was[0].cost, yield_pages=was[0].yield_pages
                        )
                    )
            periods.append((start, priced))
        timelines[printer_id] = _timeline(periods)

    return timelines


@dataclass(frozen=True)
class DistrictRates:
    """The district-wide rates in force during one period.

    Paper travels with the per-page rates rather than in a timeline of its own:
    they are set together on one screen, they change together on one date, and
    two timelines built from the same table is one rule written down twice.
    """

    formulas: FormulaValues
    cost_per_sheet_paper: float


async def load_district_rate_timeline(
    db: AsyncSession, settings: ReportFormulaSettings
) -> _Timeline:
    """The district flat rates over time.

    The non-money constants — sheets per tree, CO2 per sheet — ride along
    unchanged in every period. They are not prices and have no history; putting
    today's values in each period says only that PrintOps has always computed
    trees the same way, which is true.
    """
    history = (await db.execute(select(DistrictCostRateHistory))).scalars().all()

    def rates(mono: float, color: float, paper: float) -> DistrictRates:
        return DistrictRates(
            formulas=FormulaValues(
                cost_per_page_mono=mono,
                cost_per_page_color=color,
                sheets_per_tree=settings.sheets_per_tree,
                co2_grams_per_sheet=settings.co2_grams_per_sheet,
            ),
            cost_per_sheet_paper=paper,
        )

    periods: list[tuple[date, object]] = [
        (
            row.effective_from,
            rates(row.cost_per_page_mono, row.cost_per_page_color, row.cost_per_sheet_paper),
        )
        for row in history
    ]
    periods.append(
        (
            settings.rates_effective_from,
            rates(
                settings.cost_per_page_mono,
                settings.cost_per_page_color,
                settings.cost_per_sheet_paper,
            ),
        )
    )
    return _timeline(periods)


# --- recording a change ------------------------------------------------------
#
# The write counterpart of the timelines above, in the same module on purpose:
# how a period is closed and how it is later read back are one rule, and this
# codebase's recurring defect is one rule living in two places.


@dataclass(frozen=True)
class PreviousPrice:
    """What a slot cost before this save, for next_price_period below."""

    cost: float
    yield_pages: int
    priced_from: date


def next_price_period(
    db: AsyncSession,
    *,
    printer_id: UUID,
    color: str,
    previous: PreviousPrice | None,
    cost: float,
    yield_pages: int,
    today: date,
) -> date:
    """The priced_from the slot's current row should now carry, closing the
    period behind it when the price actually moved.

    The one place that decides when a price boundary exists. Both cartridge
    write paths use it: the per-printer PUT, which deletes the whole set and
    rebuilds it, and the fleet bulk edit, which updates rows in place.

    A slot being priced for the **first** time is open-ended backwards rather
    than dated today. The admin is stating what this cartridge costs, and
    absent any other information that is the best estimate for last term too —
    exactly as it was before rates had dates. Only a *change* to a price PrintOps
    already held is an event, because only then is there a previous belief to
    record.
    """
    if previous is None:
        return date(1970, 1, 1)
    if previous.cost == cost and previous.yield_pages == yield_pages:
        return previous.priced_from
    # Repriced twice on one day: the first value never applied to a whole day,
    # and under a date model there is no such thing as part of one. Overwrite
    # rather than record an empty period that every lookup would have to skip.
    if previous.priced_from >= today:
        return previous.priced_from

    db.add(
        PrinterTonerPriceHistory(
            printer_id=printer_id,
            color=color,
            cost=previous.cost,
            yield_pages=previous.yield_pages,
            effective_from=previous.priced_from,
            effective_to=today,
        )
    )
    return today


def record_cartridge_price_change(
    db: AsyncSession,
    cartridge: PrinterTonerCartridge,
    cost: float,
    yield_pages: int,
    today: date,
) -> None:
    """Move an existing slot to a new price in place, keeping what it cost
    before — the same rule as next_price_period, applied to a live row."""
    cartridge.priced_from = next_price_period(
        db,
        printer_id=cartridge.printer_id,
        color=cartridge.color,
        previous=PreviousPrice(
            cost=cartridge.cost,
            yield_pages=cartridge.yield_pages,
            priced_from=cartridge.priced_from,
        ),
        cost=cost,
        yield_pages=yield_pages,
        today=today,
    )
    cartridge.cost = cost
    cartridge.yield_pages = yield_pages


def record_district_rate_change(
    db: AsyncSession,
    settings: ReportFormulaSettings,
    *,
    cost_per_page_mono: float,
    cost_per_page_color: float,
    cost_per_sheet_paper: float,
    today: date,
) -> None:
    """The same, for the three district-wide money figures.

    All three move together because they are stored and edited together; a
    period in which only paper changed simply repeats the other two, which is
    what was true of it.
    """
    unchanged = (
        settings.cost_per_page_mono == cost_per_page_mono
        and settings.cost_per_page_color == cost_per_page_color
        and settings.cost_per_sheet_paper == cost_per_sheet_paper
    )
    if unchanged:
        return

    if settings.rates_effective_from < today:
        db.add(
            DistrictCostRateHistory(
                cost_per_page_mono=settings.cost_per_page_mono,
                cost_per_page_color=settings.cost_per_page_color,
                cost_per_sheet_paper=settings.cost_per_sheet_paper,
                effective_from=settings.rates_effective_from,
                effective_to=today,
            )
        )
        settings.rates_effective_from = today

    settings.cost_per_page_mono = cost_per_page_mono
    settings.cost_per_page_color = cost_per_page_color
    settings.cost_per_sheet_paper = cost_per_sheet_paper
