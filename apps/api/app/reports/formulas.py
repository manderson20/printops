"""Turns raw job facts (already aggregated by app/reports/aggregation.py)
into cost/environmental estimates, using the admin-configurable constants in
ReportFormulaSettings. Pure functions of already-computed numbers — nothing
here touches the database, so it's directly unit-testable and reusable by
both live reports and saved snapshots (a snapshot just calls this once and
freezes the result — see app/models/report.py:ReportSnapshot)."""

import math
from dataclasses import dataclass
from typing import Protocol

from app.reports.aggregation import SummaryTotals, physical_sheets_used


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


# --- Real per-printer toner + paper cost (app/reports/aggregation.py's
# get_cost_breakdown) — a more accurate replacement for the flat
# cost_per_page_mono/color estimate above, once a printer's actual
# cartridges are entered. Kept separate from compute_environmental_impact
# rather than folded in: that function works off pre-aggregated
# SummaryTotals, but per-job physical sheet rounding (ceil(pages/2) per
# job, not per aggregate sum) means accurate cost has to be computed one
# job row at a time — see the module-level docstring in aggregation.py's
# get_cost_breakdown for why.


@dataclass
class PrinterTonerRate:
    mono_cost_per_page: float
    color_cost_per_page: float


class CartridgeLike(Protocol):
    """Structural type, not sqlalchemy-specific — compute_printer_rate only
    needs `.color`/`.cost`/`.yield_pages`, so tests can pass plain objects
    instead of real ORM rows."""

    color: str
    cost: float
    yield_pages: int


CARTRIDGE_COLORS = ("black", "cyan", "magenta", "yellow")


def compute_printer_rate(
    cartridges: list[CartridgeLike], fallback: FormulaValues
) -> PrinterTonerRate:
    """Mono prices off the black cartridge alone; color prices off all 4
    summed (the standard "worst-case click cost" model) — confirmed with
    the user rather than assumed. Any color slot missing a configured
    cartridge (yield_pages of 0 would divide by zero, so also treated as
    "not configured") falls back to the flat admin-set rate for that
    color mode, so cost estimates never go blank mid-rollout."""
    by_color = {c.color: c for c in cartridges if c.yield_pages > 0}

    black = by_color.get("black")
    mono_rate = (black.cost / black.yield_pages) if black else fallback.cost_per_page_mono

    if all(color in by_color for color in CARTRIDGE_COLORS):
        color_rate = sum(
            by_color[color].cost / by_color[color].yield_pages for color in CARTRIDGE_COLORS
        )
    else:
        color_rate = fallback.cost_per_page_color

    return PrinterTonerRate(mono_cost_per_page=mono_rate, color_cost_per_page=color_rate)


@dataclass
class JobCost:
    toner_cost: float
    sheets: int
    paper_cost: float
    total_cost: float


def job_cost(
    page_count: int,
    color_mode: str | None,
    duplex: bool | None,
    rate: PrinterTonerRate,
    cost_per_sheet_paper: float,
) -> JobCost:
    """Real cost for one job — toner priced per page at that printer's
    actual rate (color rate if color_mode == "color", else the mono rate;
    an unreported color_mode prices at mono, the same conservative rule
    compute_environmental_impact already uses for unknown-color-mode
    pages), plus paper at physical sheets actually consumed."""
    toner_rate = rate.color_cost_per_page if color_mode == "color" else rate.mono_cost_per_page
    toner_cost = page_count * toner_rate
    sheets = physical_sheets_used(page_count, duplex)
    paper_cost = sheets * cost_per_sheet_paper
    return JobCost(
        toner_cost=toner_cost,
        sheets=sheets,
        paper_cost=paper_cost,
        total_cost=toner_cost + paper_cost,
    )
