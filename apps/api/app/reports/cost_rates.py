"""Loads each printer's real toner cost-per-page rate for cost
calculations (app/reports/formulas.py:job_cost) — split into its own
module rather than living in aggregation.py or formulas.py: it does a DB
query (like aggregation.py's get_cost_raw_rows) but also calls
compute_printer_rate (formulas.py), and formulas.py already imports from
aggregation.py (physical_sheets_used) — putting this here avoids that
would-be circular import while still letting both app/routers/reports.py
and app/routers/jobs.py share one implementation instead of duplicating
it."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import PrinterTonerCartridge
from app.reports.formulas import (
    ChannelRates,
    FormulaValues,
    PrinterTonerRate,
    compute_printer_rate,
)


async def _cartridges_by_printer(
    db: AsyncSession, printer_ids: set[UUID]
) -> dict[UUID, list[PrinterTonerCartridge]]:
    """The one query behind both rate loaders below, so they cannot disagree
    about which cartridges belong to a printer."""
    if not printer_ids:
        return {}
    result = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id.in_(printer_ids))
    )
    by_printer: dict[UUID, list[PrinterTonerCartridge]] = {}
    for cartridge in result.scalars().all():
        by_printer.setdefault(cartridge.printer_id, []).append(cartridge)
    return by_printer


async def load_printer_rates(
    db: AsyncSession, printer_ids: set[UUID], fallback: FormulaValues
) -> dict[UUID, PrinterTonerRate]:
    """One printer's cartridges price both its mono and color pages — see
    app/reports/formulas.py:compute_printer_rate for the fallback rule
    when a printer has no (or incomplete) cartridges configured yet."""
    by_printer = await _cartridges_by_printer(db, printer_ids)
    return {
        printer_id: compute_printer_rate(by_printer.get(printer_id, []), fallback)
        for printer_id in printer_ids
    }


async def load_printer_channel_rates(
    db: AsyncSession, printer_ids: set[UUID]
) -> dict[UUID, ChannelRates | None]:
    """What one page of each colorant costs, per printer, for costing a job by
    its measured coverage.

    Separate from load_printer_rates because the answer is different in kind:
    that one always produces a rate, falling back to the district flat figure,
    while this one is None unless all four cartridges are priced — there is no
    honest way to split a flat colour rate into four channels that cost
    different amounts. measured_toner_cost accepts that None and says so.
    """
    by_printer = await _cartridges_by_printer(db, printer_ids)
    return {
        printer_id: ChannelRates.from_cartridges(by_printer.get(printer_id, []))
        for printer_id in printer_ids
    }
