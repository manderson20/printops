"""Resolving a named period into dates, and naming it back.

Pure — no database, no clock. Everything here takes the calendar, the terms and
"today" as arguments, the same reason `resolve_period` always took `today`: it
makes the awkward cases (a year that spans New Year, a term overridden for one
year only) unit-testable without a fixture.

Three kinds of period, and the distinction matters:

**Universal.** `week`, `month`, `calendar_year`. True for everybody, belong to
nobody's calendar, need no configuration. The calendar year is here on purpose
even for an organisation whose reporting year starts in July — a budget
conversation and a calendar-year comparison are different questions, and
somebody will want both.

**The reporting year.** One per organisation, starting where they say. A school
year and a fiscal year are the same object with a different noun.

**Terms.** The parts of a reporting year — semesters, quarters, trimesters, or
nothing at all. Generated from a repeating pattern so no year needs data
entered for it, with dated overrides for the year whose dates really differed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Universal period keys, resolvable without any configuration at all.
WEEK = "week"
MONTH = "month"
CALENDAR_YEAR = "calendar_year"
UNIVERSAL_PERIODS = (WEEK, MONTH, CALENDAR_YEAR)

# The organisation's own year, and the term containing today.
REPORTING_YEAR = "year"
CURRENT_TERM = "term"

# What the old hardcoded tuple called things. Kept working because the web app
# sends them and people have bookmarked them; "semester" is simply the term
# containing today, whatever this organisation calls its terms.
LEGACY_ALIASES = {"semester": CURRENT_TERM, "year": REPORTING_YEAR}


@dataclass(frozen=True)
class CalendarSpec:
    """The organisation's year, as plain values rather than an ORM row."""

    year_start_month: int = 7
    year_start_day: int = 1
    year_noun: str = "Year"
    label_style: str = "auto"

    @classmethod
    def from_row(cls, row) -> CalendarSpec:
        if row is None:
            return cls()
        return cls(
            year_start_month=row.year_start_month,
            year_start_day=row.year_start_day,
            year_noun=row.year_noun,
            label_style=row.label_style,
        )


@dataclass(frozen=True)
class TermSpec:
    """One term of the repeating pattern."""

    name: str
    start_month: int
    start_day: int
    position: int


@dataclass(frozen=True)
class TermOverride:
    """One term of one particular year, with real dates. `end` is inclusive,
    the way a school publishes its calendar; converted once here."""

    reporting_year: int
    name: str
    start: date
    end: date
    position: int | None


@dataclass(frozen=True)
class Period:
    """A resolved period: what to call it, and what it covers.

    `end` is **exclusive**, so a period is `start <= d < end` and two adjacent
    periods cannot both claim the same day. The inclusive form people write
    calendars in is converted at the edges rather than carried around, because
    an off-by-one on a period boundary silently moves a day of printing from
    one term to another and nothing complains.
    """

    key: str
    label: str
    start: date
    end: date
    kind: str

    def contains(self, day: date) -> bool:
        return self.start <= day < self.end


def reporting_year_of(day: date, calendar: CalendarSpec) -> int:
    """Which reporting year `day` falls in, named by the year it starts in.

    A July-start year running July 2026 to June 2027 is 2026 throughout,
    including in the following January — which is the whole reason this
    function exists rather than `day.year` being used directly.
    """
    boundary = date(day.year, calendar.year_start_month, calendar.year_start_day)
    return day.year if day >= boundary else day.year - 1


def reporting_year_bounds(year: int, calendar: CalendarSpec) -> tuple[date, date]:
    start = date(year, calendar.year_start_month, calendar.year_start_day)
    end = date(year + 1, calendar.year_start_month, calendar.year_start_day)
    return start, end


def effective_style(calendar: CalendarSpec) -> str:
    """ "auto" resolved. A year beginning in January is one number; any other
    start spans two and saying so avoids an ambiguity nobody can recover from
    downstream."""
    if calendar.label_style != "auto":
        return calendar.label_style
    return "single" if calendar.year_start_month == 1 else "spanning"


def year_designation(year: int, calendar: CalendarSpec) -> int:
    """The single number this organisation puts on a reporting year.

    For a January start it is the year itself. For any other start it is the
    year it *ends* in, which is what FY2027 means for an October start — and
    the one case where deriving it from the start would be confidently wrong.
    """
    return year if calendar.year_start_month == 1 else year + 1


def year_label(year: int, calendar: CalendarSpec) -> str:
    """ "School year 2026–2027", or "Fiscal year 2027", or "Year 2026"."""
    if effective_style(calendar) == "spanning":
        return f"{calendar.year_noun} {year}–{year + 1}"
    return f"{calendar.year_noun} {year_designation(year, calendar)}"


def term_label(name: str, start: date, year: int, calendar: CalendarSpec) -> str:
    """What year to put after a term's name — and the two answers differ.

    A school's terms are named for seasons, and a season carries its own
    calendar year: the year running July 2026 to June 2027 contains "Fall 2026"
    and "Spring 2027", and calling the second one 2026 would be wrong on its
    face.

    A business's quarters are named for their position in the year, and all
    four belong to the same one: Q1 through Q4 of an October-start year are all
    FY2027, including the two that fall in calendar 2027 and the two that do
    not. Numbering them by their own calendar year would split a single
    financial year across two labels.

    Both follow from `label_style`, which the organisation has already set to
    say whether its year is one number or two — so this needs no second setting
    and cannot disagree with the year's own label.
    """
    if effective_style(calendar) == "spanning":
        return f"{name} {start.year}"
    return f"{name} {year_designation(year, calendar)}"


def reporting_year_period(year: int, calendar: CalendarSpec) -> Period:
    start, end = reporting_year_bounds(year, calendar)
    return Period(
        key=f"year:{year}",
        label=year_label(year, calendar),
        start=start,
        end=end,
        kind="year",
    )


def calendar_year_period(year: int) -> Period:
    return Period(
        key=f"calendar_year:{year}",
        label=f"Calendar year {year}",
        start=date(year, 1, 1),
        end=date(year + 1, 1, 1),
        kind="calendar_year",
    )


def terms_for_year(
    year: int,
    calendar: CalendarSpec,
    terms: list[TermSpec],
    overrides: list[TermOverride] | None = None,
) -> list[Period]:
    """Every term of one reporting year, in date order.

    Overrides for a year replace the pattern for that year **entirely**. Mixing
    them would leave a year that is half generated and half stated, where the
    gaps and overlaps depend on which rows happen to exist — so an admin
    correcting one term is asked for that year's terms, and gets a year they
    can read off the screen.
    """
    year_start, year_end = reporting_year_bounds(year, calendar)

    stated = [o for o in (overrides or []) if o.reporting_year == year]
    if stated:
        ordered = sorted(stated, key=lambda o: o.start)
        return [
            Period(
                # `position` where the override declares one, so a key means
                # the same thing whether a year came from the pattern or from
                # stated dates. Falling back to the index would make
                # `term:2026:0` mean "position 0" in one year and "the first
                # one listed" in the next, and "the same term last year" is
                # answered by matching these keys.
                key=f"term:{year}:{o.position if o.position is not None else index}",
                label=term_label(o.name, o.start, year, calendar),
                start=o.start,
                # Stored inclusive, resolved exclusive — see Period.
                end=o.end + timedelta(days=1),
                kind="term",
            )
            for index, o in enumerate(ordered)
        ]

    if not terms:
        return []

    # A term's month/day is placed in whichever calendar year puts it inside
    # this reporting year. For a July-start year, a term beginning in August
    # falls in the starting calendar year and one beginning in January falls in
    # the next.
    placed: list[tuple[date, TermSpec]] = []
    for term in terms:
        candidate = date(year_start.year, term.start_month, term.start_day)
        if candidate < year_start:
            candidate = date(year_start.year + 1, term.start_month, term.start_day)
        if candidate >= year_end:
            # Configured outside its own year — a term dated before the year
            # opens. Skipped rather than silently wrapped into the next one.
            continue
        placed.append((candidate, term))

    placed.sort(key=lambda pair: pair[0])
    periods: list[Period] = []
    for index, (start, term) in enumerate(placed):
        end = placed[index + 1][0] if index + 1 < len(placed) else year_end
        periods.append(
            Period(
                key=f"term:{year}:{term.position}",
                label=term_label(term.name, start, year, calendar),
                start=start,
                end=end,
                kind="term",
            )
        )
    return periods


def current_term(
    today: date,
    calendar: CalendarSpec,
    terms: list[TermSpec],
    overrides: list[TermOverride] | None = None,
) -> Period | None:
    year = reporting_year_of(today, calendar)
    for period in terms_for_year(year, calendar, terms, overrides):
        if period.contains(today):
            return period
    return None


def resolve(
    key: str,
    today: date,
    calendar: CalendarSpec,
    terms: list[TermSpec] | None = None,
    overrides: list[TermOverride] | None = None,
) -> Period:
    """A period key to a resolved period.

    Accepts the universal names, the organisation's own `year` and `term`, the
    old `semester`, and an explicit instance key like `year:2025` or
    `term:2025:0` — which is what makes looking at an earlier period possible
    at all.

    Raises ValueError for anything else, so a bad key is a 4xx at the edge
    rather than a silently empty report.
    """
    terms = terms or []
    key = LEGACY_ALIASES.get(key, key)

    if key == WEEK:
        start = today - timedelta(days=today.weekday())
        return Period(key, "This week", start, today + timedelta(days=1), "week")
    if key == MONTH:
        start = date(today.year, today.month, 1)
        return Period(key, "This month", start, today + timedelta(days=1), "month")
    if key == CALENDAR_YEAR:
        return calendar_year_period(today.year)
    if key == REPORTING_YEAR:
        return reporting_year_period(reporting_year_of(today, calendar), calendar)
    if key == CURRENT_TERM:
        found = current_term(today, calendar, terms, overrides)
        if found is not None:
            return found
        # No terms configured, or today falls in a gap between stated ones.
        # The year is the honest answer — better than an empty range that
        # would read as "you printed nothing".
        return reporting_year_period(reporting_year_of(today, calendar), calendar)

    kind, _, rest = key.partition(":")
    if rest:
        try:
            if kind == "calendar_year":
                return calendar_year_period(int(rest))
            if kind == "year":
                return reporting_year_period(int(rest), calendar)
            if kind == "term":
                year_text, _, position = rest.partition(":")
                year = int(year_text)
                for period in terms_for_year(year, calendar, terms, overrides):
                    if period.key == key:
                        return period
                raise ValueError(f"no term {position!r} in {year}")
        except ValueError as exc:
            raise ValueError(f"unknown period {key!r}: {exc}") from exc

    raise ValueError(f"unknown period {key!r}")


def clamp_to_today(period: Period, today: date) -> tuple[date, date]:
    """(start, end) for querying, with the end never past today.

    The current year should not claim to cover months that have not happened —
    a report saying "this year" and silently including next June would divide
    every average by the wrong number of days. Returns an inclusive end,
    because that is what ReportFilters and every existing caller expect.
    """
    last_day = min(period.end - timedelta(days=1), today)
    return period.start, max(period.start, last_day)
