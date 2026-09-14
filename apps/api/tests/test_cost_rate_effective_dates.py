"""When a price applied, and what that changes.

Every cost figure used to be computed at today's rates. Harmless while a report
only looks at the current period, and wrong the moment two are compared: a
year-on-year figure would reprice last year at this year's toner prices and
report the difference as a change in printing.

These cover the rule itself — when a price boundary exists. The end-to-end
behaviour, that a job printed before a repricing is still costed at what it
cost then, is in test_reports_api.py where the API fixtures live.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.report import PrinterTonerPriceHistory
from app.reports import rate_history
from app.reports.rate_history import (
    PreviousPrice,
    _price_periods,
    _Timeline,
    close_removed_price,
    district_today,
    next_price_period,
    priced_on,
)

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


def test_a_detected_placeholder_is_not_a_previous_price():
    """The SNMP poll creates a cost-0, yield-0 row the moment it sees a colour.
    That was never a price anyone believed, so the first real one entered over
    it is open-ended backwards like any other first price — not dated today
    with every earlier job pushed onto the flat fallback."""
    placeholder = PreviousPrice(cost=0.0, yield_pages=0, priced_from=date(1970, 1, 1))
    priced_from, history = period(previous=placeholder, cost=20.0)
    assert priced_from == date(1970, 1, 1)
    assert history == []


def test_removing_a_priced_colour_closes_its_period():
    db = FakeSession()
    was = PreviousPrice(cost=30.0, yield_pages=1500, priced_from=date(2025, 1, 1))

    close_removed_price(db, printer_id=PRINTER, color="cyan", previous=was, today=TODAY)

    [row] = db.added
    assert (row.color, row.cost, row.yield_pages) == ("cyan", 30.0, 1500)
    assert (row.effective_from, row.effective_to) == (date(2025, 1, 1), TODAY)


@pytest.mark.parametrize(
    "was",
    [
        PreviousPrice(cost=0.0, yield_pages=0, priced_from=date(1970, 1, 1)),
        PreviousPrice(cost=30.0, yield_pages=1500, priced_from=TODAY),
    ],
    ids=["placeholder", "priced-today"],
)
def test_removing_a_colour_that_never_held_a_whole_day_records_nothing(was):
    db = FakeSession()
    close_removed_price(db, printer_id=PRINTER, color="cyan", previous=was, today=TODAY)
    assert db.added == []


def _slot(color, cost, priced_from, yield_pages=1000):
    return SimpleNamespace(color=color, cost=cost, yield_pages=yield_pages, priced_from=priced_from)


def _past(color, cost, effective_from, effective_to, yield_pages=1000):
    return SimpleNamespace(
        color=color,
        cost=cost,
        yield_pages=yield_pages,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def _priced(timeline, day):
    return {c.color: c.cost for c in timeline.on(day)}


def test_a_removed_colour_still_prices_the_days_it_was_in_the_set():
    """Cyan was taken out of the set on 8 September. A report for June has to
    price cyan at what it cost in June; one for today has no cyan at all."""
    timeline = _price_periods(
        slots=[_slot("black", 20.0, date(1970, 1, 1))],
        history=[_past("cyan", 30.0, date(2025, 1, 1), TODAY)],
    )

    assert _priced(timeline, date(2026, 6, 1)) == {"black": 20.0, "cyan": 30.0}
    assert _priced(timeline, TODAY) == {"black": 20.0}


def test_a_repriced_colour_still_reads_its_old_price_before_the_change():
    timeline = _price_periods(
        slots=[_slot("black", 26.0, date(2026, 1, 1))],
        history=[_past("black", 20.0, date(2025, 1, 1), date(2026, 1, 1))],
    )

    assert _priced(timeline, date(2025, 6, 1)) == {"black": 20.0}
    assert _priced(timeline, date(2026, 6, 1)) == {"black": 26.0}


CHICAGO = ZoneInfo("America/Chicago")


def test_a_job_is_priced_on_the_local_day_it_was_sent_when_it_went_straight_out():
    # 02:30 UTC on the 9th is still the evening of the 8th in Chicago.
    sent = datetime(2026, 9, 9, 2, 30, tzinfo=UTC)
    assert priced_on(sent, None, CHICAGO) == date(2026, 9, 8)


def test_a_held_job_is_priced_on_the_day_it_was_released():
    """Sent on the last day of the old price, released on the first of the new:
    it printed under the new one."""
    sent = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    released = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    assert priced_on(sent, released, CHICAGO) == date(2026, 9, 1)


async def test_a_price_change_is_dated_in_the_districts_calendar(monkeypatch):
    """Kiritimati is fourteen hours ahead of UTC, so for most of every day its
    date is not UTC's. A price saved there has to take effect on its own date."""
    kiritimati = ZoneInfo("Pacific/Kiritimati")

    async def fake_zone(_db):
        return kiritimati

    monkeypatch.setattr(rate_history, "district_zone", fake_zone)
    assert await district_today(None) == datetime.now(kiritimati).date()
