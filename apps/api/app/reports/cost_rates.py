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
from app.reports.formulas import FormulaValues, PrinterTonerRate, compute_printer_rate


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
