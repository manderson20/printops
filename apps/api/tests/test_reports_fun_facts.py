from datetime import date

from app.reports.aggregation import LeaderboardEntry, PeakTimes, SummaryTotals, TimelineBucket
from app.reports.equivalency import Equivalency, build_equivalencies, pick_milestone
from app.reports.equivalency_config import DISTANCE_LADDER, STACK_LADDER
from app.reports.formulas import EnvironmentalImpact
from app.reports.fun_facts import (
    duplex_opportunity_fact,
    generate_equivalency_facts,
    generate_fun_facts,
)


def _environmental(**overrides):
    defaults = dict(
        estimated_cost_mono=0.0,
        estimated_cost_color=0.0,
        estimated_cost_total=0.0,
        sheets_of_paper=0,
        duplex_sheets_saved=0,
        trees_used=0.0,
        co2_grams=0.0,
    )
    defaults.update(overrides)
    return EnvironmentalImpact(**defaults)


def test_busiest_day_fact_picks_highest_page_bucket():
    timeline = [
        TimelineBucket(bucket_start=date(2026, 3, 3), total_pages=100),
        TimelineBucket(bucket_start=date(2026, 3, 5), total_pages=3420),
    ]
    facts = generate_fun_facts(
        SummaryTotals(total_jobs=1, total_pages=3520),
        timeline,
        PeakTimes(),
        [],
        _environmental(),
    )
    assert any("Thursday, March 5" in f and "3,420 pages" in f for f in facts)


def test_color_percentage_fact():
    summary = SummaryTotals(total_jobs=1, total_pages=100, color_pages=18)
    facts = generate_fun_facts(summary, [], PeakTimes(), [], _environmental(), period_label="month")
    assert any("Color printing made up 18% of total pages this month." == f for f in facts)


def test_duplex_savings_fact_present_when_positive():
    facts = generate_fun_facts(
        SummaryTotals(total_jobs=1, total_pages=1),
        [],
        PeakTimes(),
        [],
        _environmental(duplex_sheets_saved=1240),
    )
    assert any("1,240 sheets of paper" in f for f in facts)


def test_duplex_savings_fact_absent_when_zero():
    summary = SummaryTotals(total_jobs=1, total_pages=1)
    facts = generate_fun_facts(summary, [], PeakTimes(), [], _environmental(duplex_sheets_saved=0))
    assert not any("sheets of paper" in f for f in facts)


def test_top_printer_fact():
    summary = SummaryTotals(total_jobs=100, total_pages=1)
    leaderboard = [
        LeaderboardEntry(key="1", label="HS-Library-Copier", job_count=31, total_pages=500)
    ]
    facts = generate_fun_facts(summary, [], PeakTimes(), leaderboard, _environmental())
    assert any("Printer HS-Library-Copier handled 31% of all jobs." == f for f in facts)


def test_period_over_period_fact_reports_decrease():
    summary = SummaryTotals(total_jobs=1, total_pages=780)
    previous = SummaryTotals(total_jobs=1, total_pages=1000)
    facts = generate_fun_facts(
        summary,
        [],
        PeakTimes(),
        [],
        _environmental(),
        previous_summary=previous,
        period_label="month",
    )
    assert any("printed 22% less than last month." in f for f in facts)


def test_period_over_period_fact_absent_without_previous():
    summary = SummaryTotals(total_jobs=1, total_pages=1)
    facts = generate_fun_facts(summary, [], PeakTimes(), [], _environmental())
    assert not any("than last" in f for f in facts)


def test_peak_hours_fact():
    peak = PeakTimes(by_hour={8: 500, 9: 900, 14: 100})
    facts = generate_fun_facts(
        SummaryTotals(total_jobs=1, total_pages=1), [], peak, [], _environmental()
    )
    assert any("between 9:00 AM and 10:00 AM" in f for f in facts)


def test_no_facts_when_everything_empty():
    facts = generate_fun_facts(SummaryTotals(), [], PeakTimes(), [], _environmental())
    assert facts == []


# --- "Your Printing, Explained" equivalency sentences -------------------

# District scale, from the real figures this feature was built against:
# 118,952 combined pages across 116,048 sheets.
DISTRICT_PAGES = 118_952
DISTRICT_SHEETS = 116_048


def _district_facts():
    return generate_equivalency_facts(
        build_equivalencies(DISTRICT_PAGES, DISTRICT_SHEETS), collective=True
    )


def _personal_facts(pages=1_096, sheets=1_093):
    return generate_equivalency_facts(build_equivalencies(pages, sheets), collective=False)


def test_district_facts_speak_collectively():
    joined = " ".join(_district_facts())
    assert "we" in joined.lower()
    assert "your" not in joined.lower()


def test_personal_facts_address_the_reader():
    joined = " ".join(_personal_facts())
    assert "your" in joined.lower()
    assert "together we" not in joined.lower()


def test_distance_fact_names_the_rung_passed_and_the_next_one_short():
    fact = next(f for f in _district_facts() if "stretch" in f)
    assert "Brookfield to Marceline" in fact
    # The upcoming rung uses its short form, so the sentence doesn't read
    # "...the way to Brookfield to Jefferson City".
    assert "to Jefferson City" in fact
    assert "to Brookfield to Jefferson City" not in fact


def test_stack_fact_uses_taller_than_phrasing():
    fact = next(f for f in _district_facts() if "Stacked up" in f)
    assert "taller than" in fact


def test_student_fact_is_district_only():
    assert any("every student" in f for f in _district_facts())
    assert not any("every student" in f for f in _personal_facts())


def test_topped_out_ladder_reads_as_an_achievement_not_a_bar():
    # Enough pages to pass every distance rung, including the Moon.
    facts = generate_equivalency_facts(
        build_equivalencies(pages=10_000_000_000, sheets=10_000_000_000), collective=True
    )
    fact = next(f for f in facts if "stretch" in f)
    assert "all the way to the Moon!" in fact
    assert "% of the way" not in fact


def test_near_empty_progress_is_omitted_rather_than_shown_as_zero():
    # Just past the football field, a rounding error away from the track.
    milestone = pick_milestone(1_041.0, DISTANCE_LADDER)
    assert milestone.show_progress is True
    # And a total barely off zero suppresses the clause entirely.
    tiny = generate_equivalency_facts(
        [Equivalency("distance", 1.0, "feet", pick_milestone(1.0, DISTANCE_LADDER))],
        collective=True,
    )
    assert "% of the way" not in tiny[0]


def test_no_equivalencies_produces_no_sentences():
    assert generate_equivalency_facts([], collective=True) == []


def test_equivalency_without_a_milestone_still_gets_a_sentence():
    fact = generate_equivalency_facts([Equivalency("trees", 14.0, "trees")], collective=True)
    assert fact == ["That's about 14 trees' worth of paper."]


def test_stack_fact_survives_a_ladder_it_has_not_reached():
    milestone = pick_milestone(0.01, STACK_LADDER)
    fact = generate_equivalency_facts(
        [Equivalency("stack_height", 0.01, "feet", milestone)], collective=False
    )
    assert fact[0].endswith("tall.")


# --- duplex framing ----------------------------------------------------


def test_duplex_opportunity_is_framed_as_available_not_wasted():
    fact = duplex_opportunity_fact(additional_sheets=545, saved_sheets=0)
    assert "would save" in fact
    for judgement in ("wasted", "too much", "should have"):
        assert judgement not in fact.lower()


def test_duplex_credits_an_existing_saving_alongside_the_opportunity():
    fact = duplex_opportunity_fact(additional_sheets=545, saved_sheets=3)
    assert "already saved 3 sheets" in fact
    assert "another 545" in fact


def test_duplex_with_nothing_left_to_save_credits_the_saving_only():
    fact = duplex_opportunity_fact(additional_sheets=0, saved_sheets=120)
    assert fact == "Double-siding has already saved 120 sheets."


def test_duplex_says_nothing_when_there_is_nothing_to_say():
    assert duplex_opportunity_fact(additional_sheets=0, saved_sheets=0) is None
