from app.reports.aggregation import SummaryTotals
from app.reports.formulas import FormulaValues, compute_environmental_impact


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
