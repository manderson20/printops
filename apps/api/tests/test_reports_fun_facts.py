from datetime import date

from app.reports.aggregation import LeaderboardEntry, PeakTimes, SummaryTotals, TimelineBucket
from app.reports.formulas import EnvironmentalImpact
from app.reports.fun_facts import generate_fun_facts


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
