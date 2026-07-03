"""Deterministic, template-based fun-fact generation for Print Insights —
no LLM call. Every sentence is derived from numbers already computed by
app/reports/aggregation.py and formulas.py, so this stays cheap, instant,
and directly unit-testable. A fact is simply omitted (not printed as "0%" or
similar) when the underlying data can't support a meaningful sentence — e.g.
no jobs in range, or no previous-period comparison available."""

from app.reports.aggregation import LeaderboardEntry, PeakTimes, SummaryTotals, TimelineBucket
from app.reports.formulas import EnvironmentalImpact


def _format_hour(hour: int) -> str:
    period = "AM" if hour < 12 else "PM"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display}:00 {period}"


def _busiest_day_fact(timeline: list[TimelineBucket]) -> str | None:
    candidates = [b for b in timeline if b.total_pages > 0]
    if not candidates:
        return None
    busiest = max(candidates, key=lambda b: b.total_pages)
    weekday = busiest.bucket_start.strftime("%A")
    month = busiest.bucket_start.strftime("%B")
    date_label = f"{weekday}, {month} {busiest.bucket_start.day}"
    return f"Your busiest print day was {date_label} with {busiest.total_pages:,} pages."


def _color_percentage_fact(summary: SummaryTotals, period_label: str) -> str | None:
    if summary.total_pages == 0:
        return None
    pct = round(summary.color_pages / summary.total_pages * 100)
    return f"Color printing made up {pct}% of total pages this {period_label}."


def _duplex_savings_fact(environmental: EnvironmentalImpact) -> str | None:
    if environmental.duplex_sheets_saved <= 0:
        return None
    saved = environmental.duplex_sheets_saved
    return f"Duplex printing saved an estimated {saved:,} sheets of paper."


def _top_printer_fact(summary: SummaryTotals, leaderboard: list[LeaderboardEntry]) -> str | None:
    if not leaderboard or summary.total_jobs == 0:
        return None
    top = leaderboard[0]
    pct = round(top.job_count / summary.total_jobs * 100)
    return f"Printer {top.label} handled {pct}% of all jobs."


def _period_over_period_fact(
    summary: SummaryTotals, previous_summary: SummaryTotals | None, period_label: str
) -> str | None:
    if previous_summary is None or previous_summary.total_pages == 0:
        return None
    change_pct = round(
        (summary.total_pages - previous_summary.total_pages) / previous_summary.total_pages * 100
    )
    if change_pct == 0:
        return f"Printing volume was flat compared to last {period_label}."
    direction = "more" if change_pct > 0 else "less"
    return f"The district printed {abs(change_pct)}% {direction} than last {period_label}."


def _peak_hours_fact(peak_times: PeakTimes) -> str | None:
    if not peak_times.by_hour:
        return None
    peak_hour = max(peak_times.by_hour, key=lambda h: peak_times.by_hour[h])
    start = _format_hour(peak_hour)
    end = _format_hour((peak_hour + 1) % 24)
    return f"Most printing happened between {start} and {end}."


def generate_fun_facts(
    summary: SummaryTotals,
    timeline: list[TimelineBucket],
    peak_times: PeakTimes,
    printer_leaderboard: list[LeaderboardEntry],
    environmental: EnvironmentalImpact,
    previous_summary: SummaryTotals | None = None,
    period_label: str = "period",
) -> list[str]:
    facts = [
        _busiest_day_fact(timeline),
        _color_percentage_fact(summary, period_label),
        _duplex_savings_fact(environmental),
        _top_printer_fact(summary, printer_leaderboard),
        _period_over_period_fact(summary, previous_summary, period_label),
        _peak_hours_fact(peak_times),
    ]
    return [fact for fact in facts if fact is not None]
