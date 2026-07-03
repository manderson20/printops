from dataclasses import dataclass

from app.reports.aggregation import SummaryTotals
from app.reports.formulas import (
    FormulaValues,
    compute_environmental_impact,
    compute_printer_rate,
    job_cost,
)


@dataclass
class FakeCartridge:
    color: str
    cost: float
    yield_pages: int


def _formulas(**overrides):
    defaults = dict(
        cost_per_page_mono=0.03,
        cost_per_page_color=0.10,
        sheets_per_tree=100.0,
        co2_grams_per_sheet=5.0,
    )
    defaults.update(overrides)
    return FormulaValues(**defaults)


def test_cost_split_between_color_and_mono():
    summary = SummaryTotals(
        total_pages=100, color_pages=30, mono_pages=60, unknown_color_mode_pages=10
    )
    impact = compute_environmental_impact(summary, _formulas())
    # unknown pages priced at the (cheaper) mono rate
    assert impact.estimated_cost_mono == round((60 + 10) * 0.03, 2)
    assert impact.estimated_cost_color == round(30 * 0.10, 2)
    expected_total = round(impact.estimated_cost_mono + impact.estimated_cost_color, 2)
    assert impact.estimated_cost_total == expected_total


def test_duplex_sheets_saved_and_paper_totals():
    summary = SummaryTotals(
        total_pages=110, duplex_pages=100, simplex_pages=10, unknown_duplex_pages=0
    )
    impact = compute_environmental_impact(summary, _formulas())
    # 100 duplex pages -> 50 physical sheets, saving the other 50
    assert impact.duplex_sheets_saved == 50
    assert impact.sheets_of_paper == 50 + 10


def test_unknown_duplex_treated_as_simplex():
    summary = SummaryTotals(
        total_pages=20, duplex_pages=0, simplex_pages=0, unknown_duplex_pages=20
    )
    impact = compute_environmental_impact(summary, _formulas())
    assert impact.sheets_of_paper == 20
    assert impact.duplex_sheets_saved == 0


def test_trees_and_co2_scale_with_sheets():
    summary = SummaryTotals(total_pages=200, simplex_pages=200)
    formulas = _formulas(sheets_per_tree=100.0, co2_grams_per_sheet=5.0)
    impact = compute_environmental_impact(summary, formulas)
    assert impact.sheets_of_paper == 200
    assert impact.trees_used == 2.0
    assert impact.co2_grams == 1000.0


def test_zero_sheets_per_tree_does_not_divide_by_zero():
    summary = SummaryTotals(total_pages=10, simplex_pages=10)
    impact = compute_environmental_impact(summary, _formulas(sheets_per_tree=0.0))
    assert impact.trees_used == 0.0


def test_printer_rate_uses_black_cartridge_for_mono():
    cartridges = [FakeCartridge("black", cost=60.0, yield_pages=3000)]
    rate = compute_printer_rate(cartridges, _formulas())
    assert rate.mono_cost_per_page == 0.02
    # CMY not configured -> color falls back to the flat rate.
    assert rate.color_cost_per_page == 0.10


def test_printer_rate_sums_all_four_cartridges_for_color():
    cartridges = [
        FakeCartridge("black", cost=60.0, yield_pages=3000),   # 0.02/page
        FakeCartridge("cyan", cost=80.0, yield_pages=2000),    # 0.04/page
        FakeCartridge("magenta", cost=80.0, yield_pages=2000), # 0.04/page
        FakeCartridge("yellow", cost=80.0, yield_pages=2000),  # 0.04/page
    ]
    rate = compute_printer_rate(cartridges, _formulas())
    assert rate.mono_cost_per_page == 0.02
    assert round(rate.color_cost_per_page, 4) == round(0.02 + 0.04 * 3, 4)


def test_printer_rate_falls_back_when_no_cartridges_configured():
    rate = compute_printer_rate([], _formulas(cost_per_page_mono=0.05, cost_per_page_color=0.20))
    assert rate.mono_cost_per_page == 0.05
    assert rate.color_cost_per_page == 0.20


def test_printer_rate_ignores_zero_yield_cartridge():
    # A cartridge someone entered with yield_pages=0 (not filled in yet)
    # must not divide by zero or count as "configured".
    cartridges = [FakeCartridge("black", cost=60.0, yield_pages=0)]
    rate = compute_printer_rate(cartridges, _formulas(cost_per_page_mono=0.05))
    assert rate.mono_cost_per_page == 0.05


def test_job_cost_prices_color_job_at_color_rate():
    rate = compute_printer_rate(
        [
            FakeCartridge("black", cost=40.0, yield_pages=2000),
            FakeCartridge("cyan", cost=40.0, yield_pages=2000),
            FakeCartridge("magenta", cost=40.0, yield_pages=2000),
            FakeCartridge("yellow", cost=40.0, yield_pages=2000),
        ],
        _formulas(),
    )
    result = job_cost(
        page_count=10, color_mode="color", duplex=False, rate=rate, cost_per_sheet_paper=0.01
    )
    assert result.toner_cost == round(10 * 0.08, 10)
    assert result.sheets == 10
    assert result.paper_cost == 0.1
    assert round(result.total_cost, 4) == round(0.8 + 0.1, 4)


def test_job_cost_unknown_color_mode_prices_at_mono_rate():
    cartridges = [FakeCartridge("black", cost=20.0, yield_pages=1000)]
    rate = compute_printer_rate(cartridges, _formulas())
    result = job_cost(
        page_count=5, color_mode=None, duplex=False, rate=rate, cost_per_sheet_paper=0.0
    )
    assert result.toner_cost == 5 * 0.02


def test_job_cost_duplex_halves_sheets():
    rate = compute_printer_rate([], _formulas(cost_per_page_mono=0.0))
    result = job_cost(
        page_count=10, color_mode="monochrome", duplex=True, rate=rate, cost_per_sheet_paper=1.0
    )
    assert result.sheets == 5
    assert result.paper_cost == 5.0
