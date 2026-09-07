"""Turning a period name into dates, and back into words.

The bugs this guards against are quiet ones. A term boundary off by a day moves
printing from one semester to another and nothing complains; a year labelled
with the wrong number puts that number on every report an administrator reads.
Neither shows up as an error, so they are asserted here instead.
"""

from datetime import date, timedelta

import pytest

from app.reports.periods import (
    CalendarSpec,
    TermOverride,
    TermSpec,
    clamp_to_today,
    current_term,
    reporting_year_of,
    resolve,
    terms_for_year,
    year_label,
)

# A school on a July-start year with two semesters.
SCHOOL = CalendarSpec(year_start_month=7, year_start_day=1, year_noun="School year")
SEMESTERS = [
    TermSpec(name="Fall Semester", start_month=8, start_day=15, position=0),
    TermSpec(name="Spring Semester", start_month=1, start_day=5, position=1),
]

# A business on an October-start fiscal year with four quarters, labelled by the
# year it ends in — FY2027 runs October 2026 to September 2027.
BUSINESS = CalendarSpec(year_start_month=10, year_start_day=1, year_noun="FY", label_style="single")
QUARTERS = [
    TermSpec(name="Q1", start_month=10, start_day=1, position=0),
    TermSpec(name="Q2", start_month=1, start_day=1, position=1),
    TermSpec(name="Q3", start_month=4, start_day=1, position=2),
    TermSpec(name="Q4", start_month=7, start_day=1, position=3),
]

CALENDAR = CalendarSpec(year_start_month=1, year_start_day=1, year_noun="Year")


# --- which year is it -------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected", "because"),
    [
        (date(2026, 7, 1), 2026, "the day the year opens"),
        (date(2026, 6, 30), 2025, "the day before it opens is still the old year"),
        (date(2027, 1, 15), 2026, "January is the middle of a July-start year, not a new one"),
        (date(2027, 6, 30), 2026, "the last day"),
    ],
)
def test_a_year_that_spans_new_year_keeps_its_number(day, expected, because):
    """The whole reason this is not `day.year`."""
    assert reporting_year_of(day, SCHOOL) == expected, because


# --- the four shapes --------------------------------------------------------


def test_two_semesters():
    periods = terms_for_year(2026, SCHOOL, SEMESTERS)

    assert [(p.label, p.start, p.end) for p in periods] == [
        ("Fall Semester 2026", date(2026, 8, 15), date(2027, 1, 5)),
        # Ends where the reporting year ends, not on 31 December.
        ("Spring Semester 2027", date(2027, 1, 5), date(2027, 7, 1)),
    ]


def test_four_quarters_of_a_fiscal_year():
    """A term dated January belongs to the *second half* of an October-start
    year — it does not open a new one."""
    periods = terms_for_year(2026, BUSINESS, QUARTERS)

    assert [(p.label, p.start) for p in periods] == [
        ("Q1 2027", date(2026, 10, 1)),
        ("Q2 2027", date(2027, 1, 1)),
        ("Q3 2027", date(2027, 4, 1)),
        ("Q4 2027", date(2027, 7, 1)),
    ]
    assert periods[-1].end == date(2027, 10, 1), "the last term closes the year"


def test_all_four_quarters_of_one_fiscal_year_carry_the_same_number():
    """Q1 falls in calendar 2026 and Q3 in calendar 2027, but both are FY2027.
    Numbering each by its own calendar year would split one financial year
    across two labels and make a year-on-year comparison read as nonsense."""
    labels = {p.label.split()[-1] for p in terms_for_year(2026, BUSINESS, QUARTERS)}
    assert labels == {"2027"}


def test_a_schools_terms_are_named_for_their_own_calendar_year():
    """The other half of the same rule: "Spring 2026" would be plainly wrong for
    a term running January to June 2027."""
    fall, spring = terms_for_year(2026, SCHOOL, SEMESTERS)
    assert fall.label.endswith("2026")
    assert spring.label.endswith("2027")


def test_three_trimesters():
    trimesters = [
        TermSpec(name="Trimester 1", start_month=8, start_day=20, position=0),
        TermSpec(name="Trimester 2", start_month=11, start_day=20, position=1),
        TermSpec(name="Trimester 3", start_month=3, start_day=1, position=2),
    ]
    periods = terms_for_year(2026, SCHOOL, trimesters)

    assert len(periods) == 3
    assert periods[0].start == date(2026, 8, 20)
    assert periods[2].start == date(2027, 3, 1)
    assert periods[2].end == date(2027, 7, 1)


def test_an_organisation_with_no_terms_has_none():
    """A supported shape, not an unconfigured one."""
    assert terms_for_year(2026, SCHOOL, []) == []


def test_terms_are_contiguous_and_do_not_overlap():
    """Each term ends exactly where the next begins, so no day of printing is
    counted twice or falls through the gap."""
    periods = terms_for_year(2026, BUSINESS, QUARTERS)
    for earlier, later in zip(periods, periods[1:]):
        assert earlier.end == later.start


# --- labels -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("calendar", "expected", "because"),
    [
        (SCHOOL, "School year 2026–2027", "a July start spans two years, so say both"),
        (CALENDAR, "Year 2026", "a January start is one year"),
        (BUSINESS, "FY 2027", "an explicit single style names the year it ends in"),
        (
            CalendarSpec(year_start_month=1, year_start_day=1, label_style="spanning"),
            "Year 2026–2027",
            "an explicit style wins over what the start month would imply",
        ),
    ],
)
def test_year_labels(calendar, expected, because):
    assert year_label(2026, calendar) == expected, because


# --- overriding one year ----------------------------------------------------


def test_stated_dates_replace_the_pattern_for_that_year_only():
    """The year a semester really did start late. Every other year still comes
    from the pattern, so one correction does not become an annual chore."""
    overrides = [
        TermOverride(
            reporting_year=2026,
            name="Fall Semester",
            start=date(2026, 8, 22),
            end=date(2026, 12, 20),
            position=0,
        ),
        TermOverride(
            reporting_year=2026,
            name="Spring Semester",
            start=date(2027, 1, 9),
            end=date(2027, 5, 26),
            position=1,
        ),
    ]

    stated = terms_for_year(2026, SCHOOL, SEMESTERS, overrides)
    assert [p.start for p in stated] == [date(2026, 8, 22), date(2027, 1, 9)]
    # Inclusive end date in, exclusive bound out — the day after the last day.
    assert stated[0].end == date(2026, 12, 21)

    generated = terms_for_year(2027, SCHOOL, SEMESTERS, overrides)
    assert [p.start for p in generated] == [date(2027, 8, 15), date(2028, 1, 5)]


def test_an_overridden_year_leaves_real_gaps_alone():
    """Stated dates are stated: the break between semesters is not printing
    time, and silently stretching Fall to meet Spring would invent it."""
    overrides = [
        TermOverride(2026, "Fall", date(2026, 8, 22), date(2026, 12, 20), 0),
        TermOverride(2026, "Spring", date(2027, 1, 9), date(2027, 5, 26), 1),
    ]
    fall, spring = terms_for_year(2026, SCHOOL, SEMESTERS, overrides)
    assert fall.end < spring.start
    assert not fall.contains(date(2026, 12, 28)), "the winter break is in neither term"


# --- resolving --------------------------------------------------------------


def test_the_old_period_names_still_work():
    """The web app sends these and people have bookmarked them. A bookmark must
    not 500 because an organisation renamed its terms."""
    today = date(2026, 10, 15)

    assert resolve("semester", today, SCHOOL, SEMESTERS).label == "Fall Semester 2026"
    assert resolve("year", today, SCHOOL, SEMESTERS).label == "School year 2026–2027"
    assert resolve("week", today, SCHOOL).start == date(2026, 10, 12)
    assert resolve("month", today, SCHOOL).start == date(2026, 10, 1)


def test_the_calendar_year_is_available_even_to_a_july_start_organisation():
    """A budget conversation and a calendar-year comparison are different
    questions, and somebody will want both."""
    period = resolve("calendar_year", date(2026, 10, 15), SCHOOL)
    assert (period.start, period.end) == (date(2026, 1, 1), date(2027, 1, 1))
    assert period.label == "Calendar year 2026"


def test_an_earlier_period_can_be_named_directly():
    """What makes looking at last year possible at all."""
    today = date(2026, 10, 15)

    assert resolve("year:2024", today, SCHOOL).start == date(2024, 7, 1)
    assert resolve("calendar_year:2023", today, SCHOOL).start == date(2023, 1, 1)
    assert resolve("term:2025:1", today, SCHOOL, SEMESTERS).label == "Spring Semester 2026"


def test_semester_falls_back_to_the_year_rather_than_nothing():
    """Between terms, or with none configured. An empty range would render as
    "you printed nothing", which is a different and wrong claim."""
    resolved = resolve("semester", date(2026, 7, 20), SCHOOL, SEMESTERS)
    assert resolved.kind == "year", "before Fall starts, the year is the honest answer"

    none_configured = resolve("semester", date(2026, 10, 15), SCHOOL, [])
    assert none_configured.kind == "year"


@pytest.mark.parametrize(
    "key", ["quarter", "", "year:", "term:2026", "term:2026:9", "year:notanumber"]
)
def test_an_unknown_period_is_refused(key):
    """So a bad key is a 4xx at the edge, not a silently empty report."""
    with pytest.raises(ValueError):
        resolve(key, date(2026, 10, 15), SCHOOL, SEMESTERS)


def test_current_term_returns_nothing_between_terms():
    assert current_term(date(2026, 7, 20), SCHOOL, SEMESTERS) is None


# --- not counting days that have not happened -------------------------------


def test_the_current_year_does_not_claim_months_that_have_not_happened():
    """A report saying "this year" that silently includes next June divides
    every average by the wrong number of days."""
    today = date(2026, 10, 15)
    start, end = clamp_to_today(resolve("year", today, SCHOOL), today)
    assert (start, end) == (date(2026, 7, 1), today)


def test_a_finished_period_keeps_its_own_end():
    today = date(2026, 10, 15)
    start, end = clamp_to_today(resolve("year:2024", today, SCHOOL), today)
    assert (start, end) == (date(2024, 7, 1), date(2025, 6, 30)), "inclusive last day"


# --- nothing moves on upgrade -----------------------------------------------


def test_the_seeded_calendar_reproduces_the_retiring_resolution_exactly():
    """Migration 0084 turns the compiled-in Fall/Spring split into two term
    rows. This asserts the translation is faithful for every day of two years —
    not for a handful of chosen dates, because the failure this guards against
    is a single misplaced boundary that moves one day of printing from one
    semester to the other and reports nothing.

    Delete this test when `equivalency.resolve_period` goes; it exists to date
    the changeover, not to specify the new behaviour.
    """
    from app.reports.equivalency import SchoolCalendar, resolve_period

    old = SchoolCalendar(
        year_start_month=7, year_start_day=1, spring_start_month=1, spring_start_day=1
    )
    # Exactly what _seed() writes for this calendar.
    seeded_terms = [
        TermSpec(name="Fall Semester", start_month=7, start_day=1, position=0),
        TermSpec(name="Spring Semester", start_month=1, start_day=1, position=1),
    ]

    day = date(2026, 1, 1)
    while day < date(2028, 1, 1):
        for period in ("week", "month", "semester", "year"):
            was_start, was_end = resolve_period(period, day, old)
            now_start, now_end = clamp_to_today(resolve(period, day, SCHOOL, seeded_terms), day)
            assert (now_start, now_end) == (was_start, was_end), f"{period} on {day}"
        day += timedelta(days=1)


def test_an_overridden_term_keeps_the_key_its_pattern_counterpart_has():
    """A period key has to mean the same thing in every year.

    Stage 3 answers "compared with the same term last year" by matching these
    keys across years. If an overridden year numbered its terms by list order
    while a generated year numbered them by position, the comparison would
    quietly line up Fall against Spring in any year where the two disagreed.
    """
    overrides = [
        TermOverride(2026, "Spring Semester", date(2027, 1, 9), date(2027, 5, 26), 1),
        TermOverride(2026, "Fall Semester", date(2026, 8, 22), date(2026, 12, 20), 0),
    ]

    stated = {p.key: p.label for p in terms_for_year(2026, SCHOOL, SEMESTERS, overrides)}
    generated = {p.key: p.label for p in terms_for_year(2027, SCHOOL, SEMESTERS, overrides)}

    assert stated["term:2026:0"].startswith("Fall")
    assert generated["term:2027:0"].startswith("Fall")
    assert stated["term:2026:1"].startswith("Spring")
    assert generated["term:2027:1"].startswith("Spring")
