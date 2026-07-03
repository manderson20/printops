"""Turns raw job facts (already aggregated by app/reports/aggregation.py)
into cost/environmental estimates, using the admin-configurable constants in
ReportFormulaSettings. Pure functions of already-computed numbers — nothing
here touches the database, so it's directly unit-testable and reusable by
both live reports and saved snapshots (a snapshot just calls this once and
freezes the result — see app/models/report.py:ReportSnapshot)."""

import math
from dataclasses import dataclass

from app.reports.aggregation import SummaryTotals


@dataclass
class FormulaValues:
    cost_per_page_mono: float
    cost_per_page_color: float
    sheets_per_tree: float
    co2_grams_per_sheet: float


@dataclass
class EnvironmentalImpact:
    estimated_cost_mono: float
    estimated_cost_color: float
    estimated_cost_total: float
    sheets_of_paper: int
    duplex_sheets_saved: int
    trees_used: float
    co2_grams: float


def compute_environmental_impact(
    summary: SummaryTotals, formulas: FormulaValues
) -> EnvironmentalImpact:
    # Unknown-color-mode pages are priced at the (cheaper) mono rate — a
    # conservative assumption so a gap in printer-reported data doesn't
    # inflate the cost estimate.
    mono_priced_pages = summary.mono_pages + summary.unknown_color_mode_pages
    estimated_cost_mono = mono_priced_pages * formulas.cost_per_page_mono
    estimated_cost_color = summary.color_pages * formulas.cost_per_page_color
    estimated_cost_total = estimated_cost_mono + estimated_cost_color

    # Physical sheets consumed: duplex jobs use ~half a sheet per page,
    # simplex (and unknown-duplex, treated as simplex — see
    # aggregation.physical_sheets_used) use one. duplex_pages/simplex_pages
    # here are already page counts, so convert the duplex portion.
    duplex_sheets = math.ceil(summary.duplex_pages / 2)
    simplex_sheets = summary.simplex_pages + summary.unknown_duplex_pages
    sheets_of_paper = duplex_sheets + simplex_sheets

    # How many extra sheets those duplex pages would have used if printed
    # simplex instead — i.e. the paper actually saved by duplexing.
    duplex_sheets_saved = summary.duplex_pages - duplex_sheets

    trees_used = (
        round(sheets_of_paper / formulas.sheets_per_tree, 4) if formulas.sheets_per_tree else 0.0
    )
    return EnvironmentalImpact(
        estimated_cost_mono=round(estimated_cost_mono, 2),
        estimated_cost_color=round(estimated_cost_color, 2),
        estimated_cost_total=round(estimated_cost_total, 2),
        sheets_of_paper=sheets_of_paper,
        duplex_sheets_saved=duplex_sheets_saved,
        trees_used=trees_used,
        co2_grams=round(sheets_of_paper * formulas.co2_grams_per_sheet, 1),
    )
