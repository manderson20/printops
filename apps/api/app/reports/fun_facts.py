"""Deterministic, template-based fun-fact generation for Print Insights —
no LLM call. Every sentence is derived from numbers already computed by
app/reports/aggregation.py and formulas.py, so this stays cheap, instant,
and directly unit-testable. A fact is simply omitted (not printed as "0%" or
similar) when the underlying data can't support a meaningful sentence — e.g.
no jobs in range, or no previous-period comparison available."""

from app.reports.aggregation import LeaderboardEntry, PeakTimes, SummaryTotals, TimelineBucket
from app.reports.equivalency_config import FEET_PER_MILE, INCHES_PER_FOOT
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


# --- "Your Printing, Explained" ----------------------------------------
#
# The equivalency sentences. Kept in this module rather than beside the
# arithmetic in app/reports/equivalency.py for the reason the two were
# split in the first place: this file owns the district's voice, and one
# equivalency has to read as "your stack" on the personal page and "our
# stack" on the lobby screen without the numbers being computed twice.
#
# Tone rule, applied throughout: state the fact, never the judgement.
# Nothing here says "too much", and the duplex sentence is phrased as an
# opportunity that still exists rather than paper already wasted.


def _number(value: float) -> str:
    """Human-scale rounding. Big numbers get separators and no decimals;
    small ones keep a single decimal, because "0.5 miles" is a fact and
    "0 miles" is a rounding error presented as one."""
    if value >= 1000:
        return f"{round(value):,}"
    if value >= 100:
        return f"{round(value):,}"
    if value >= 10:
        return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"
    return f"{value:.1f}"


def _distance_phrase(feet: float) -> str:
    if feet >= FEET_PER_MILE:
        return f"{_number(feet / FEET_PER_MILE)} miles"
    return f"{_number(feet)} feet"


def _height_phrase(feet: float) -> str:
    if feet < 1:
        return f"{_number(feet * INCHES_PER_FOOT)} inches"
    return f"{_number(feet)} feet"


def _weight_phrase(pounds: float) -> str:
    if pounds >= 2000:
        return f"{_number(pounds / 2000)} tons"
    return f"{_number(pounds)} lb"


def _progress_clause(milestone, collective: bool) -> str:
    """ " — and we're 17% of the way to Jefferson City", or nothing.

    Omitted entirely when the bar would be near-empty: a sentence that
    ends "and we're 0% of the way to the Moon" reads as a failure, which
    is the one thing this feature must never do."""
    if milestone is None or not milestone.show_progress:
        return ""
    pct = round(milestone.progress * 100)
    who = "we're" if collective else "you're"
    return f", and {who} {pct}% of the way to {milestone.upcoming.label}"


def _distance_fact(equivalency, collective: bool) -> str | None:
    milestone = equivalency.milestone
    phrase = _distance_phrase(equivalency.value)
    lead = (
        f"Together we've printed enough pages to stretch {phrase}"
        if collective
        else f"Laid end to end, your pages would stretch {phrase}"
    )
    if milestone is None or milestone.passed is None:
        if milestone is not None and milestone.show_progress:
            who = "we're" if collective else "you're"
            pct = round(milestone.progress * 100)
            return f"{lead} — {who} {pct}% of the way to {milestone.upcoming.label}."
        return f"{lead}."
    if milestone.upcoming is None:
        return f"{lead} — that's {milestone.passed.name}!"
    return f"{lead} — that's {milestone.passed.name}{_progress_clause(milestone, collective)}."


def _stack_fact(equivalency, collective: bool) -> str | None:
    milestone = equivalency.milestone
    phrase = _height_phrase(equivalency.value)
    lead = (
        f"Stacked up, our paper stands {phrase} tall"
        if collective
        else f"Stacked up, your paper stands {phrase} tall"
    )
    if milestone is None or milestone.passed is None:
        return f"{lead}."
    return f"{lead} — taller than {milestone.passed.label}."


def _weight_fact(equivalency, collective: bool) -> str | None:
    milestone = equivalency.milestone
    phrase = _weight_phrase(equivalency.value)
    lead = (
        f"All that paper weighs about {phrase}" if collective else f"That's about {phrase} of paper"
    )
    if milestone is None or milestone.passed is None:
        return f"{lead}."
    return f"{lead} — heavier than {milestone.passed.label}."


def _trees_fact(equivalency, collective: bool) -> str | None:
    return f"That's about {_number(equivalency.value)} trees' worth of paper."


def _cases_fact(equivalency, collective: bool) -> str | None:
    return f"Enough to fill {_number(equivalency.value)} cases of copy paper."


def _water_fact(equivalency, collective: bool) -> str | None:
    """The water *footprint*, and the sentence has to say so.

    Most of this figure is rain that fell on the trees, not water anyone
    drew from a tap — process water is nearer a thirtieth of it. "About
    40,000 litres of water went into making it" invites a comparison with
    a kitchen sink that is wrong by more than an order of magnitude, so
    the phrase names what is being counted.
    """
    litres = equivalency.value
    if litres >= 1_000_000:
        amount = f"{_number(litres / 1_000_000)} million litres"
    else:
        amount = f"{_number(litres)} litres"
    return f"Growing and making the paper took about {amount} of water, most of it rain."


def _co2_fact(equivalency, miles_driven, collective: bool) -> str | None:
    grams = equivalency.value
    mass = f"{_number(grams / 1000)} kg" if grams >= 1000 else f"{_number(grams)} g"
    if miles_driven is None:
        return f"Producing it came to roughly {mass} of CO2."
    return (
        f"Producing it came to roughly {mass} of CO2 — about the same as "
        f"driving {_number(miles_driven.value)} miles."
    )


def _student_fact(equivalency, collective: bool) -> str | None:
    if not collective:
        return None
    return f"That's enough to hand every student {_number(equivalency.value)} sheets."


def _gutenberg_fact(equivalency, collective: bool) -> str | None:
    return (
        f"Gutenberg's press managed about 250 pages a day — it would have "
        f"needed {_number(equivalency.value)} days to keep up."
    )


def generate_equivalency_facts(equivalencies, *, collective: bool) -> list[str]:
    """One sentence per equivalency worth a sentence, in display order.

    `collective` switches the whole set between the district's "we" and a
    person's "you". Facts the caller's scope can't support are dropped
    rather than reworded — "enough to hand every student N sheets" is a
    district-sized claim and says nothing useful about one teacher.

    Equivalencies arrive already filtered for triviality
    (app/reports/equivalency.py:build_equivalencies), so anything absent
    here is absent because it wasn't worth showing, not because it was
    zero.
    """
    by_key = {e.key: e for e in equivalencies}
    miles_driven = by_key.get("miles_driven")

    builders = [
        ("distance", lambda e: _distance_fact(e, collective)),
        ("stack_height", lambda e: _stack_fact(e, collective)),
        ("weight", lambda e: _weight_fact(e, collective)),
        ("trees", lambda e: _trees_fact(e, collective)),
        ("cases", lambda e: _cases_fact(e, collective)),
        ("water", lambda e: _water_fact(e, collective)),
        ("co2", lambda e: _co2_fact(e, miles_driven, collective)),
        ("sheets_per_student", lambda e: _student_fact(e, collective)),
        ("gutenberg_days", lambda e: _gutenberg_fact(e, collective)),
    ]

    facts = []
    for key, build in builders:
        equivalency = by_key.get(key)
        if equivalency is None:
            continue
        fact = build(equivalency)
        if fact is not None:
            facts.append(fact)
    return facts


def duplex_opportunity_fact(additional_sheets: int, saved_sheets: int) -> str | None:
    """The savings sentence, framed as something still available rather
    than something already lost — the one place this feature could tip
    into shaming, so it says what duplexing *would* do and, when there is
    one, credits the saving already made."""
    if additional_sheets <= 0:
        if saved_sheets > 0:
            return f"Double-siding has already saved {saved_sheets:,} sheets."
        return None
    if saved_sheets > 0:
        return (
            f"Double-siding has already saved {saved_sheets:,} sheets — "
            f"another {additional_sheets:,} are still on the table."
        )
    return f"Printing double-sided would save about {additional_sheets:,} more sheets."
