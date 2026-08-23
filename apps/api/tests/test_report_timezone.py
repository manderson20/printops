"""Reports are read in the district's own timezone, not UTC.

Timestamps are stored in UTC, which is right and unchanged. What was wrong is
that the reports were also *read* that way: get_peak_times counted the hour and
weekday straight off the stored value, so "busiest hour" was five or six hours
out and anything printed after 7pm was attributed to the next weekday, while
get_timeline bucketed on the UTC date so an evening's printing moved into the
next day's total. None of it looked wrong on screen.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.reports.aggregation import _bucket_key, local

CHICAGO = ZoneInfo("America/Chicago")

# 2026-08-23 21:40 in Chicago is already the 24th in UTC — the boundary every
# evening of printing falls across.
EVENING = datetime(2026, 8, 24, 2, 40, tzinfo=UTC)


def test_an_evening_job_belongs_to_the_day_it_was_printed():
    assert EVENING.date().isoformat() == "2026-08-24", "UTC calls this tomorrow"
    assert _bucket_key(EVENING, "day", CHICAGO).isoformat() == "2026-08-23"


def test_an_evening_job_belongs_to_the_weekday_it_was_printed():
    # Sunday in Chicago, Monday in UTC.
    assert EVENING.weekday() == 0
    assert local(EVENING, CHICAGO).weekday() == 6


def test_the_hour_is_the_hour_someone_was_standing_at_the_printer():
    assert local(EVENING, CHICAGO).hour == 21


def test_a_naive_timestamp_is_read_as_utc_not_as_local():
    """SQLite hands back naive datetimes in the test suite. Treating those as
    already-local would move every one of them by the offset."""
    naive = EVENING.replace(tzinfo=None)
    assert local(naive, CHICAGO).hour == local(EVENING, CHICAGO).hour


def test_a_week_bucket_starts_on_the_local_monday():
    # Sunday evening in Chicago belongs to the week beginning Monday the 17th,
    # not the one beginning the 24th that UTC would give it.
    assert _bucket_key(EVENING, "week", CHICAGO).isoformat() == "2026-08-17"


def test_a_month_bucket_follows_the_local_date_too():
    # 2026-09-01 00:30 UTC is still August in Chicago.
    end_of_august = datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    assert _bucket_key(end_of_august, "month", CHICAGO).isoformat() == "2026-08-01"


def test_daylight_saving_is_handled_by_the_zone_not_by_arithmetic():
    """A fixed -5 or -6 offset would be wrong for half the year. In 2026 US
    DST ends on 1 November."""
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    winter = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    assert local(summer, CHICAGO).hour == 7  # CDT, UTC-5
    assert local(winter, CHICAGO).hour == 6  # CST, UTC-6


def test_utc_is_still_available_for_a_caller_that_wants_it():
    assert _bucket_key(EVENING, "day", ZoneInfo("UTC")).isoformat() == "2026-08-24"


def test_a_days_worth_of_jobs_lands_in_one_bucket():
    """The shape of the bug: a school day that starts at 7am and ends at 9pm
    spans two UTC dates and must not be split across two rows of the report."""
    start = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)  # 07:00 Chicago
    keys = {
        _bucket_key(start + timedelta(hours=h), "day", CHICAGO).isoformat() for h in range(0, 15)
    }
    assert keys == {"2026-08-23"}
