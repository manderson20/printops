"""Unit tests for the pure equivalency layer — no DB, no fixtures, same
shape as test_reports_formulas.py."""

from datetime import date

import pytest

from app.reports.aggregation import SummaryTotals
from app.reports.equivalency import (
    Equivalency,
    build_equivalencies,
    distance_feet,
    duplex_sheets_saved,
    pick_milestone,
    progress_to_next,
    resolve_period,
    sheets_from_totals,
    sheets_if_all_duplex,
    stack_height_feet,
    weight_pounds,
)
from app.reports.equivalency_config import (
    DISTANCE_LADDER,
    FEET_PER_MILE,
    STACK_LADDER,
    WEIGHT_LADDER,
    Ladder,
    Milestone,
)
from app.reports.formulas import FormulaValues, compute_environmental_impact

# --- sheet conversion --------------------------------------------------


def test_duplex_pages_share_a_sheet():
    assert sheets_from_totals(duplex_pages=10) == 5


def test_odd_duplex_page_count_rounds_up_to_a_whole_sheet():
    # 7 duplexed pages need 4 sheets, not 3.5 — you cannot use half a sheet.
    assert sheets_from_totals(duplex_pages=7) == 4


def test_unknown_duplex_is_counted_as_simplex():
    assert sheets_from_totals(unknown_duplex_pages=10) == 10


def test_copy_pages_are_counted_as_simplex():
    # Counter-derived copy pages carry no duplex flag at all.
    assert sheets_from_totals(copy_pages=40) == 40


def test_sheet_conversion_agrees_with_formulas_module():
    """The rule exists in two places for two different questions; this
    fails the moment they drift apart."""
    summary = SummaryTotals(
        total_jobs=0,
        total_pages=0,
        color_pages=0,
        mono_pages=0,
        unknown_color_mode_pages=0,
        duplex_pages=57,
        simplex_pages=311,
        unknown_duplex_pages=12,
    )
    formulas = FormulaValues(
        cost_per_page_mono=0.03,
        cost_per_page_color=0.10,
        sheets_per_tree=8300.0,
        co2_grams_per_sheet=4.6,
    )
    impact = compute_environmental_impact(summary, formulas)
    assert impact.sheets_of_paper == sheets_from_totals(
        duplex_pages=57, simplex_pages=311, unknown_duplex_pages=12
    )


def test_negative_page_counts_are_floored_at_zero():
    # A counter delta should never go negative, but a meter reset could
    # produce one — it must not subtract from the total.
    assert sheets_from_totals(duplex_pages=-10, simplex_pages=5) == 5


def test_duplex_sheets_saved_is_the_difference_against_simplex():
    assert duplex_sheets_saved(10) == 5
    assert duplex_sheets_saved(7) == 3
    assert duplex_sheets_saved(0) == 0


def test_sheets_if_all_duplex_excludes_copies_by_construction():
    # No copy_pages parameter exists — the opportunity figure is print-only.
    assert sheets_if_all_duplex(simplex_pages=100) == 50


# --- milestone selection -----------------------------------------------

TINY = Ladder(
    key="tiny",
    unit="units",
    rungs=(Milestone("first", 10.0), Milestone("second", 20.0), Milestone("third", 40.0)),
)


def test_below_the_first_rung_nothing_is_passed():
    result = pick_milestone(4.0, TINY)
    assert result.passed is None
    assert result.upcoming.name == "first"
    assert result.progress == pytest.approx(0.4)


def test_between_rungs_reports_the_highest_passed_and_the_next():
    result = pick_milestone(25.0, TINY)
    assert result.passed.name == "second"
    assert result.upcoming.name == "third"
    # Measured from zero, not from the previous rung: 25/40, not 5/20.
    assert result.progress == pytest.approx(0.625)


def test_landing_exactly_on_a_rung_counts_as_passed():
    result = pick_milestone(20.0, TINY)
    assert result.passed.name == "second"
    assert result.upcoming.name == "third"


def test_past_the_top_has_no_next_target_and_a_full_bar():
    result = pick_milestone(1_000.0, TINY)
    assert result.passed.name == "third"
    assert result.upcoming is None
    assert result.progress == 1.0


def test_zero_total_passes_nothing_and_makes_no_progress():
    result = pick_milestone(0.0, TINY)
    assert result.passed is None
    assert result.upcoming.name == "first"
    assert result.progress == 0.0


def test_negative_total_is_treated_like_zero():
    result = pick_milestone(-5.0, TINY)
    assert result.passed is None
    assert result.progress == 0.0


def test_empty_ladder_degrades_to_no_milestone_rather_than_raising():
    result = pick_milestone(50.0, Ladder(key="empty", unit="units", rungs=()))
    assert result.passed is None
    assert result.upcoming is None
    assert result.progress == 0.0


def test_zero_valued_rung_is_dropped_instead_of_dividing_by_zero():
    ladder = Ladder(
        key="zeroed", unit="units", rungs=(Milestone("bad", 0.0), Milestone("good", 10.0))
    )
    result = pick_milestone(5.0, ladder)
    assert result.passed is None
    assert result.upcoming.name == "good"
    assert result.progress == pytest.approx(0.5)


def test_out_of_order_rungs_are_sorted_before_selection():
    ladder = Ladder(
        key="jumbled",
        unit="units",
        rungs=(Milestone("big", 40.0), Milestone("small", 10.0), Milestone("mid", 20.0)),
    )
    result = pick_milestone(25.0, ladder)
    assert result.passed.name == "mid"
    assert result.upcoming.name == "big"


def test_single_rung_ladder_works_both_sides_of_it():
    ladder = Ladder(key="one", unit="units", rungs=(Milestone("only", 10.0),))
    assert pick_milestone(5.0, ladder).upcoming.name == "only"
    assert pick_milestone(15.0, ladder).passed.name == "only"
    assert pick_milestone(15.0, ladder).upcoming is None


def test_progress_never_exceeds_one():
    assert progress_to_next(10_000.0, TINY) == 1.0


def test_show_progress_suppresses_a_near_empty_bar():
    # 0.05 of the way to the first rung — a bar that reads as broken.
    assert pick_milestone(0.5, TINY).show_progress is True
    assert pick_milestone(0.01, TINY).show_progress is False


def test_show_progress_is_false_once_the_ladder_is_topped_out():
    assert pick_milestone(1_000.0, TINY).show_progress is False


# --- the real ladders --------------------------------------------------


def test_football_field_sorts_below_a_track_lap():
    """The brief listed the track first; the field's perimeter (1,040 ft)
    is actually shorter than a quarter-mile lap (1,320 ft). Sorting by
    value is what keeps the bar monotonic."""
    result = pick_milestone(1_200.0, DISTANCE_LADDER)
    assert result.passed.name == "a lap of the football field"
    assert result.upcoming.name == "a quarter-mile lap of the track"
    # Mid-sentence the rung is just the distance: "42% of the way to a
    # quarter mile", not "...to a quarter-mile lap of the track".
    assert result.upcoming.label == "a quarter mile"


def test_a_track_lap_is_exactly_a_quarter_mile():
    lap = next(r for r in DISTANCE_LADDER.rungs if "track" in r.name)
    assert lap.value == pytest.approx(0.25 * FEET_PER_MILE)
    assert lap.value == pytest.approx(1_320.0)


def test_a_local_ladder_places_a_district_scale_distance():
    """Local rungs are a district's own and live in the `destinations` table,
    so this passes one in rather than asserting against whichever town happened
    to be compiled into the source — which is what it used to do."""
    local = Ladder(
        key="distance",
        unit="feet",
        rungs=(
            Milestone("to the next town", 12.0 * FEET_PER_MILE, short="next town"),
            Milestone("to the state capital", 120.0 * FEET_PER_MILE, short="the capital"),
        ),
    )
    # ~118,952 pages laid end to end is a little over 20 miles.
    feet = distance_feet(118_952)
    assert feet / FEET_PER_MILE == pytest.approx(20.65, abs=0.05)

    result = pick_milestone(feet, local)
    assert result.passed.name == "to the next town"
    assert result.upcoming.name == "to the state capital"


def test_the_built_in_ladder_names_no_particular_place():
    """The fallback a fresh install gets. Somebody in another state should not
    have their printing measured against Brookfield's neighbours."""
    names = " ".join(rung.name for rung in DISTANCE_LADDER.rungs).lower()
    for local in ("brookfield", "marceline", "jefferson city", "missouri", "chicago"):
        assert local not in names


def test_personal_scale_picks_a_small_stack_rung():
    # ~1,093 sheets is a little over four inches — past one ream.
    result = pick_milestone(stack_height_feet(1_093), STACK_LADDER)
    assert result.passed.name == "a ream of paper"
    assert result.upcoming.name == "a doorway"


def test_district_scale_weight_passes_the_vending_machine():
    result = pick_milestone(weight_pounds(116_048), WEIGHT_LADDER)
    assert result.passed.name == "a vending machine"
    assert result.upcoming.name == "a pickup truck"


# --- equivalency assembly ----------------------------------------------


def _by_key(items: list[Equivalency]) -> dict[str, Equivalency]:
    return {e.key: e for e in items}


def test_zero_totals_produce_no_facts_at_all():
    assert build_equivalencies(0, 0) == []


def test_trivial_totals_drop_the_facts_that_would_round_to_zero():
    facts = _by_key(build_equivalencies(pages=3, sheets=3))
    # Three sheets is not a measurable fraction of a tree.
    assert "trees" not in facts
    # But it is still a real amount of water and a real distance.
    assert "water" in facts
    assert "distance" in facts


def test_district_scale_produces_the_full_set():
    facts = _by_key(build_equivalencies(pages=118_952, sheets=116_048))
    for key in (
        "distance",
        "stack_height",
        "weight",
        "trees",
        "reams",
        "cases",
        "water",
        "co2",
        "miles_driven",
        "gutenberg_days",
    ):
        assert key in facts, f"expected {key} at district scale"

    # Not in the list above: the per-student fact needs an enrolment figure,
    # and there is deliberately no default for it.
    assert "sheets_per_student" not in facts


def test_the_per_student_fact_needs_an_enrolment_figure():
    """It used to divide by 930 — one district's roll, compiled in — so every
    other installation was told how its printing compared to a school it had
    never heard of. Unset now means no fact, which is the honest answer."""
    without = _by_key(build_equivalencies(pages=118_952, sheets=116_048))
    assert "sheets_per_student" not in without

    with_roll = _by_key(build_equivalencies(pages=118_952, sheets=116_048, student_count=930))
    assert with_roll["sheets_per_student"].value == pytest.approx(116_048 / 930, abs=0.1)


def test_district_scale_values_match_hand_calculation():
    facts = _by_key(build_equivalencies(pages=118_952, sheets=116_048))
    # 116,048 sheets / 8,333 sheets a tree.
    assert facts["trees"].value == pytest.approx(13.93, abs=0.01)
    assert facts["reams"].value == pytest.approx(232.1, abs=0.1)
    assert facts["cases"].value == pytest.approx(23.21, abs=0.01)
    assert facts["weight"].value == pytest.approx(1_160.48, abs=0.01)
    # Sheets x 12.7 g, not pages x 5 g — the carbon is in the paper, so
    # duplexing halves it. Was 594,760 g on the old per-page basis.
    assert facts["co2"].value == pytest.approx(1473809.6, abs=0.1)
    assert facts["miles_driven"].value == pytest.approx(3684.5, abs=0.1)


def test_only_ladder_backed_facts_carry_a_milestone():
    facts = _by_key(build_equivalencies(pages=118_952, sheets=116_048))
    assert facts["distance"].milestone is not None
    assert facts["stack_height"].milestone is not None
    assert facts["weight"].milestone is not None
    assert facts["trees"].milestone is None


def test_negative_inputs_are_floored_rather_than_producing_negative_facts():
    assert build_equivalencies(pages=-100, sheets=-100) == []


# --- period math -------------------------------------------------------


def test_week_starts_on_monday():
    # 2026-09-01 is a Tuesday.
    start, end = resolve_period("week", date(2026, 9, 1))
    assert start == date(2026, 8, 31)
    assert end == date(2026, 9, 1)


def test_week_on_a_monday_starts_that_same_day():
    start, _ = resolve_period("week", date(2026, 8, 31))
    assert start == date(2026, 8, 31)


def test_month_starts_on_the_first():
    start, end = resolve_period("month", date(2026, 9, 17))
    assert start == date(2026, 9, 1)
    assert end == date(2026, 9, 17)


def test_school_year_to_date_after_the_july_start():
    start, _ = resolve_period("year", date(2026, 9, 1))
    assert start == date(2026, 7, 1)


def test_school_year_to_date_before_the_july_start_reaches_back_a_year():
    """The case that silently breaks if you assume a January year: in
    March the current school year began the previous July."""
    start, _ = resolve_period("year", date(2026, 3, 4))
    assert start == date(2025, 7, 1)


def test_school_year_starts_exactly_on_the_boundary_day():
    start, _ = resolve_period("year", date(2026, 7, 1))
    assert start == date(2026, 7, 1)


def test_autumn_semester_runs_from_the_school_year_start():
    start, _ = resolve_period("semester", date(2026, 9, 1))
    assert start == date(2026, 7, 1)


def test_spring_semester_starts_in_january():
    start, _ = resolve_period("semester", date(2026, 3, 4))
    assert start == date(2026, 1, 1)


def test_unknown_period_is_rejected_loudly():
    with pytest.raises(ValueError, match="unknown period"):
        resolve_period("fortnight", date(2026, 9, 1))
