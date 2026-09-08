"""When a price applied, and what that changes.

Every cost figure used to be computed at today's rates. Harmless while a report
only looks at the current period, and wrong the moment two are compared: a
year-on-year figure would reprice last year at this year's toner prices and
report the difference as a change in printing.

These cover the rule itself — when a price boundary exists. The end-to-end
behaviour, that a job printed before a repricing is still costed at what it
cost then, is in test_reports_api.py where the API fixtures live.
"""

from datetime import date

import pytest

from app.models.report import PrinterTonerPriceHistory
from app.reports.rate_history import PreviousPrice, _Timeline, next_price_period

PRINTER = "11111111-1111-1111-1111-111111111111"
TODAY = date(2026, 9, 8)


class FakeSession:
    """Enough of a session for next_price_period, which only ever adds."""

    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def period(previous, cost, yield_pages=1000, today=TODAY):
    db = FakeSession()
    result = next_price_period(
        db,
        printer_id=PRINTER,
        color="black",
        previous=previous,
        cost=cost,
        yield_pages=yield_pages,
        today=today,
    )
    return result, db.added


def test_a_first_price_is_open_ended_backwards():
    """An admin entering a cartridge cost is stating what it costs, and absent
    anything else that is the best estimate for last term too — which is
    exactly what PrintOps assumed before rates had dates. Dating it today would
    quietly move every historical job onto the flat fallback rate."""
    priced_from, history = period(previous=None, cost=20.0)
    assert priced_from == date(1970, 1, 1)
    assert history == [], "nothing was superseded, so there is no period to record"


def test_saving_an_unchanged_price_records_nothing():
    """Opening the form and pressing save is not a price change. Recording one
    would litter the timeline with periods across which nothing differs."""
    was = PreviousPrice(cost=20.0, yield_pages=1000, priced_from=date(2025, 1, 1))
    priced_from, history = period(previous=was, cost=20.0)
    assert priced_from == date(2025, 1, 1)
    assert history == []


def test_a_real_change_closes_the_period_behind_it():
    was = PreviousPrice(cost=20.0, yield_pages=1000, priced_from=date(2025, 1, 1))
    priced_from, history = period(previous=was, cost=26.0)

    assert priced_from == TODAY
    assert len(history) == 1
    row: PrinterTonerPriceHistory = history[0]
    assert (row.cost, row.yield_pages) == (20.0, 1000)
    # Half-open: the new price owns today, so the old one must not.
    assert row.effective_from == date(2025, 1, 1)
    assert row.effective_to == TODAY


def test_a_yield_change_is_a_price_change():
    """Cost per page is cost over yield. A cartridge switched for a
    high-capacity one at the same price is a different rate."""
    was = PreviousPrice(cost=20.0, yield_pages=1000, priced_from=date(2025, 1, 1))
    priced_from, history = period(previous=was, cost=20.0, yield_pages=3000)
    assert priced_from == TODAY
    assert len(history) == 1


def test_repricing_twice_in_one_day_does_not_record_an_empty_period():
    """A price corrected minutes after it was entered never applied to a day.
    Recording [today, today) would leave a period no lookup can ever land in,
    and a timeline that has to be defended against its own rows."""
    was = PreviousPrice(cost=26.0, yield_pages=1000, priced_from=TODAY)
    priced_from, history = period(previous=was, cost=24.0)
    assert priced_from == TODAY
    assert history == []


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 6, 1), "old"),
        (date(2025, 12, 31), "old"),
        (date(2026, 1, 1), "new"),
        (date(2026, 9, 8), "new"),
    ],
)
def test_a_lookup_lands_on_the_period_containing_the_day(day, expected):
    timeline = _Timeline(starts=[date(1970, 1, 1), date(2026, 1, 1)], values=["old", "new"])
    assert timeline.on(day) == expected


def test_a_day_before_every_recorded_price_uses_the_earliest():
    """Never None. A missing rate reads as a zero cost, and a zero cost claims
    the printing was free when the truth is only that nobody wrote the price
    down."""
    timeline = _Timeline(starts=[date(2026, 1, 1)], values=["only"])
    assert timeline.on(date(1999, 1, 1)) == "only"
