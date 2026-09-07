"""Turns page and sheet totals into the real-world equivalencies behind
"Your Printing, Explained" — distance laid end to end, stack height,
weight, trees, reams, water, CO2 — and places each on a milestone ladder.

Pure functions of already-computed numbers, exactly like
app/reports/formulas.py: nothing here touches the database or the clock,
so every branch is directly unit-testable and the same code serves the
personal view, the anonymous district view, and the admin breakdown
without any of them needing a special case.

The split against formulas.py is by *question*, not by layer. That module
answers "what did this cost and what did it consume" and is consumed by
billing-shaped reports. This one answers "what does that amount look
like", and is allowed to be approximate in ways a cost report must not
be — see app/reports/equivalency_config.py, where every figure states
whether it is exact or a public estimate.

Nothing here formats a sentence. Prose lives in app/reports/fun_facts.py,
which already owns the district's voice; keeping the numbers separate is
what lets the same equivalency read as "your stack" on one page and "our
stack" on another without recomputing anything.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta

from app.reports.equivalency_config import (
    CO2_G_PER_SHEET,
    DEFAULT_SCHOOL_YEAR_START_DAY,
    DEFAULT_SCHOOL_YEAR_START_MONTH,
    DEFAULT_SPRING_SEMESTER_START_DAY,
    DEFAULT_SPRING_SEMESTER_START_MONTH,
    G_PER_MILE_DRIVEN,
    GUTENBERG_PAGES_PER_DAY,
    INCHES_PER_FOOT,
    LADDERS,
    LB_PER_100_SHEETS,
    LITRES_PER_SHEET,
    MIN_MEANINGFUL_PROGRESS,
    MIN_MEANINGFUL_VALUE,
    MM_PER_FOOT,
    PAGE_LENGTH_IN,
    REAMS_PER_CASE,
    SHEET_THICKNESS_MM,
    SHEETS_PER_REAM,
    SHEETS_PER_TREE,
    Ladder,
    Milestone,
)

# --- sheet conversion --------------------------------------------------


def sheets_from_totals(
    *,
    duplex_pages: int = 0,
    simplex_pages: int = 0,
    unknown_duplex_pages: int = 0,
    copy_pages: int = 0,
) -> int:
    """Physical sheets consumed by an aggregate page breakdown.

    Two pages share a sheet when duplexed; everything else takes one
    each. Pages whose duplex setting nobody reported are treated as
    simplex — the same conservative rule
    app/reports/formulas.py:compute_environmental_impact applies, and
    test_reports_equivalency.py asserts the two agree so they cannot
    drift apart.

    `copy_pages` is separate because walk-up copies genuinely cannot
    carry a duplex flag: a Konica's per-account CopyCounterList has no
    per-account duplex counter at all (docs/copier-capture-konica.md
    S3.9.3), so counter-derived pages are always unknown-duplex and are
    counted as simplex. That over-states paper on a device where people
    duplex, which is the right direction to be wrong in — the same
    reasoning formulas.py:copy_cost gives for the same limitation.
    """
    duplex_sheets = math.ceil(max(duplex_pages, 0) / 2)
    flat_sheets = max(simplex_pages, 0) + max(unknown_duplex_pages, 0) + max(copy_pages, 0)
    return duplex_sheets + flat_sheets


def duplex_sheets_saved(duplex_pages: int) -> int:
    """Sheets duplexing actually saved — what those pages would have cost
    as simplex, minus what they did cost."""
    duplex_pages = max(duplex_pages, 0)
    return duplex_pages - math.ceil(duplex_pages / 2)


def sheets_if_all_duplex(
    *, duplex_pages: int = 0, simplex_pages: int = 0, unknown_duplex_pages: int = 0
) -> int:
    """Sheets the same print pages would have taken with everything
    duplexed — the "you'd have saved N more" opportunity figure. Copy
    pages are excluded because nobody chooses a duplex setting at the
    glass in a way PrintOps can see, so framing them as a missed saving
    would be blaming someone for a number we cannot observe."""
    total = max(duplex_pages, 0) + max(simplex_pages, 0) + max(unknown_duplex_pages, 0)
    return math.ceil(total / 2)


# --- milestone ladders -------------------------------------------------


@dataclass(frozen=True)
class MilestoneProgress:
    ladder_key: str
    unit: str
    total: float
    # Highest rung the total has reached, or None if it hasn't reached
    # the first one yet.
    passed: Milestone | None
    # The rung being worked toward, or None once the top is passed.
    upcoming: Milestone | None
    # How far the total has come toward `upcoming`, 0.0-1.0, measured
    # from zero rather than from `passed`. "17% of the way to Jefferson
    # City" is a claim a reader can check with one division; "8% of the
    # way from Marceline to Jefferson City" is not, and reads as a
    # smaller achievement for the same amount of paper.
    progress: float

    @property
    def show_progress(self) -> bool:
        """A bar this close to empty reads as broken rather than
        encouraging — report the milestone, skip the bar."""
        return self.upcoming is not None and self.progress >= MIN_MEANINGFUL_PROGRESS


def _usable_rungs(ladder: Ladder) -> list[Milestone]:
    """Rungs sorted by value, with non-positive ones dropped. Sorting
    defensively means adding a rung in the wrong place is a harmless
    edit; dropping non-positive ones is the division-by-zero guard for
    `progress`, since a zero-valued rung can never be a meaningful
    target."""
    return sorted((r for r in ladder.rungs if r.value > 0), key=lambda r: r.value)


def pick_milestone(total: float, ladder: Ladder) -> MilestoneProgress:
    """Where `total` sits on `ladder`.

    Handles the three shapes the views actually hit: below the first rung
    (nothing passed, working toward the first), between two rungs, and
    past the top (everything passed, no next target). An empty ladder —
    or one whose every rung was dropped as non-positive — yields a
    progress with no rungs rather than raising, so a misconfigured ladder
    degrades to a missing fact instead of a broken page.
    """
    rungs = _usable_rungs(ladder)
    if not rungs:
        return MilestoneProgress(ladder.key, ladder.unit, total, None, None, 0.0)

    if total <= 0:
        return MilestoneProgress(ladder.key, ladder.unit, total, None, rungs[0], 0.0)

    passed: Milestone | None = None
    upcoming: Milestone | None = None
    for rung in rungs:
        if total >= rung.value:
            passed = rung
        else:
            upcoming = rung
            break

    if upcoming is None:
        # Past the top of the ladder. There is no further target, so the
        # bar is full rather than undefined.
        return MilestoneProgress(ladder.key, ladder.unit, total, passed, None, 1.0)

    progress = min(total / upcoming.value, 1.0)
    return MilestoneProgress(ladder.key, ladder.unit, total, passed, upcoming, progress)


def progress_to_next(total: float, ladder: Ladder) -> float:
    """Convenience wrapper for callers that only want the fraction."""
    return pick_milestone(total, ladder).progress


# --- equivalencies -----------------------------------------------------


@dataclass(frozen=True)
class Equivalency:
    key: str
    value: float
    unit: str
    # Set only for the three ladder-backed facts.
    milestone: MilestoneProgress | None = None


def _divide(numerator: float, denominator: float) -> float:
    """Every equivalency that divides by a config constant goes through
    here. A constant edited to zero should cost one fact, not the page."""
    return numerator / denominator if denominator else 0.0


def distance_feet(pages: int) -> float:
    return _divide(max(pages, 0) * PAGE_LENGTH_IN, INCHES_PER_FOOT)


def stack_height_feet(sheets: int) -> float:
    return _divide(max(sheets, 0) * SHEET_THICKNESS_MM, MM_PER_FOOT)


def weight_pounds(sheets: int) -> float:
    return _divide(max(sheets, 0) * LB_PER_100_SHEETS, 100.0)


def build_equivalencies(
    pages: int,
    sheets: int,
    *,
    distance_ladder: Ladder | None = None,
    student_count: int = 0,
) -> list[Equivalency]:
    """Every equivalency the totals support, in display order.

    A fact whose headline would round to zero at the precision it shows
    is omitted rather than printed as "0.0 trees" — the caller can rely
    on every returned Equivalency being worth rendering, which is what
    lets the district view rotate through them without checking each one
    itself.

    `distance_ladder` is the one ladder a district owns: its road trip,
    built from the `destinations` table by
    app/reports/road_trip.py:ladder_from_destinations. It is injected
    rather than read here because this module is pure by design — it does
    not touch the database or the clock — and because passing None has to
    keep working: that is the path an installation with nothing
    configured takes, and it lands on the ladder compiled into
    equivalency_config.py. Stack height and weight stay compiled in;
    nobody's district has a different Gateway Arch.
    """
    pages = max(pages, 0)
    sheets = max(sheets, 0)

    ladders = {ladder.key: ladder for ladder in LADDERS}
    if distance_ladder is not None:
        ladders["distance"] = distance_ladder
    distance = distance_feet(pages)
    stack = stack_height_feet(sheets)
    weight = weight_pounds(sheets)

    candidates = [
        Equivalency("distance", distance, "feet", pick_milestone(distance, ladders["distance"])),
        Equivalency("stack_height", stack, "feet", pick_milestone(stack, ladders["stack_height"])),
        Equivalency("weight", weight, "pounds", pick_milestone(weight, ladders["weight"])),
        Equivalency("trees", _divide(sheets, SHEETS_PER_TREE), "trees"),
        Equivalency("reams", _divide(sheets, SHEETS_PER_REAM), "reams"),
        Equivalency("cases", _divide(_divide(sheets, SHEETS_PER_REAM), REAMS_PER_CASE), "cases"),
        Equivalency("water", sheets * LITRES_PER_SHEET, "litres"),
        # Sheets, not pages. The carbon is in the paper, so two pages
        # duplexed onto one sheet emit what one sheet emits — and charging
        # per page meant double-siding, which this same feature recommends
        # in the next breath, changed the CO2 fact not at all.
        Equivalency("co2", sheets * CO2_G_PER_SHEET, "grams"),
        Equivalency("miles_driven", _divide(sheets * CO2_G_PER_SHEET, G_PER_MILE_DRIVEN), "miles"),
        # Omitted entirely when nobody has told PrintOps how many students
        # there are. _divide returns 0 for a zero denominator and a headline
        # rounding to zero is dropped below — so an unset roll produces no fact
        # rather than one measured against a guess.
        Equivalency("sheets_per_student", _divide(sheets, student_count), "sheets per student"),
        Equivalency("gutenberg_days", _divide(pages, GUTENBERG_PAGES_PER_DAY), "days"),
    ]
    return [e for e in candidates if e.value >= MIN_MEANINGFUL_VALUE]


# --- period math -------------------------------------------------------

PERIODS = ("week", "month", "semester", "year")


@dataclass(frozen=True)
class SchoolCalendar:
    """When a district's year and spring term begin.

    A district's calendar rather than a fact about paper, so it comes from
    ReportFormulaSettings instead of being compiled in — this used to be four
    module constants, which meant every installation shared one district's term
    dates. `from_settings` keeps the defaults in one place for callers that
    have no settings row yet.
    """

    year_start_month: int = DEFAULT_SCHOOL_YEAR_START_MONTH
    year_start_day: int = DEFAULT_SCHOOL_YEAR_START_DAY
    spring_start_month: int = DEFAULT_SPRING_SEMESTER_START_MONTH
    spring_start_day: int = DEFAULT_SPRING_SEMESTER_START_DAY

    @classmethod
    def from_settings(cls, settings) -> "SchoolCalendar":
        if settings is None:
            return cls()
        return cls(
            year_start_month=settings.school_year_start_month,
            year_start_day=settings.school_year_start_day,
            spring_start_month=settings.spring_semester_start_month,
            spring_start_day=settings.spring_semester_start_day,
        )


def _school_year_start(today: date, calendar: SchoolCalendar) -> date:
    """The start of the school year `today` falls in. Before the start
    date the current school year began in the previous calendar year —
    the case that makes "school year to date" wrong for half the year if
    you assume January."""
    start_this_year = date(today.year, calendar.year_start_month, calendar.year_start_day)
    if today >= start_this_year:
        return start_this_year
    return date(today.year - 1, calendar.year_start_month, calendar.year_start_day)


def _semester_start(today: date, calendar: SchoolCalendar) -> date:
    """Fall runs from the school year start; spring from the configured spring
    date. The spring boundary only applies once the year has actually turned,
    so a date in the autumn term never resolves to a start ahead of itself."""
    year_start = _school_year_start(today, calendar)
    spring_start = date(today.year, calendar.spring_start_month, calendar.spring_start_day)
    if spring_start > year_start and today >= spring_start:
        return spring_start
    return year_start


def resolve_period(
    period: str, today: date, calendar: SchoolCalendar | None = None
) -> tuple[date, date]:
    """(start, end) for a named period, both inclusive, ending today.

    `today` is a parameter rather than read from the clock so this stays
    pure and so the caller resolves it in the district's timezone — the
    same reason app/routers/reports.py:_range_boundary takes a zone
    instead of assuming one.
    """
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return date(today.year, today.month, 1), today
    if period == "semester":
        return _semester_start(today, calendar or SchoolCalendar()), today
    if period == "year":
        return _school_year_start(today, calendar or SchoolCalendar()), today
    raise ValueError(f"unknown period {period!r} — expected one of {', '.join(PERIODS)}")
