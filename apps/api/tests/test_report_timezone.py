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


# ---- the setting itself ----


def test_the_api_refuses_a_timezone_this_server_cannot_resolve():
    """The field carried a comment saying it was validated for a whole commit
    before it actually was — and a bad zone is the worst kind of bad input
    here, because _district_zone falls back to UTC and every report goes
    quietly back to being five hours out."""
    from pydantic import ValidationError

    from app.schemas.server_settings import ServerSettingsUpdate

    assert ServerSettingsUpdate(timezone="America/Chicago").timezone == "America/Chicago"
    try:
        ServerSettingsUpdate(timezone="Not/AZone")
    except ValidationError:
        pass
    else:
        raise AssertionError("an unresolvable zone was accepted")


def test_a_padded_timezone_is_trimmed_rather_than_rejected_or_stored():
    from app.schemas.server_settings import ServerSettingsUpdate

    assert ServerSettingsUpdate(timezone="  America/Chicago  ").timezone == "America/Chicago"


def test_no_report_path_can_omit_the_zone():
    """create_snapshot omitted it and froze UTC busiest-hours into stored
    snapshots while the live report beside it used the district's zone. The
    parameter is keyword-only and has no default so that cannot recur."""
    import inspect

    from app.reports.aggregation import get_peak_times, get_timeline

    for fn in (get_timeline, get_peak_times):
        tz = inspect.signature(fn).parameters["tz"]
        assert tz.default is inspect.Parameter.empty, f"{fn.__name__} has a default zone"
        assert tz.kind is inspect.Parameter.KEYWORD_ONLY


# ---- what a date means when a report asks for one ----


def test_a_date_is_midnight_in_the_district_not_in_utc():
    """ "2026-08-23" is the day people worked. Read as UTC it would start at
    7pm the evening before and end 5 hours early."""
    from app.routers.reports import _range_boundary

    start = _range_boundary("2026-08-23", CHICAGO)
    assert start.isoformat() == "2026-08-23T05:00:00+00:00"  # CDT, UTC-5


def test_a_date_boundary_follows_daylight_saving():
    from app.routers.reports import _range_boundary

    winter = _range_boundary("2026-12-01", CHICAGO)
    assert winter.isoformat() == "2026-12-01T06:00:00+00:00"  # CST, UTC-6


def test_an_instant_is_still_taken_as_given():
    """The live dashboard computes a real local midnight client-side and sends
    it with an offset; that has to keep working untouched."""
    from app.routers.reports import _range_boundary

    exact = _range_boundary("2026-08-23T21:56:21+00:00", CHICAGO)
    assert exact.isoformat() == "2026-08-23T21:56:21+00:00"


def test_a_z_suffix_is_an_instant_too():
    from app.routers.reports import _range_boundary

    assert _range_boundary("2026-08-23T21:56:21Z", CHICAGO).isoformat() == (
        "2026-08-23T21:56:21+00:00"
    )


def test_nothing_means_no_boundary():
    from app.routers.reports import _range_boundary

    assert _range_boundary(None, CHICAGO) is None
    assert _range_boundary("", CHICAGO) is None


def test_something_that_is_neither_is_refused_rather_than_guessed_at():
    from fastapi import HTTPException

    from app.routers.reports import _range_boundary

    try:
        _range_boundary("last tuesday", CHICAGO)
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("a nonsense range was accepted")


def test_a_days_range_covers_the_whole_local_day():
    """Start inclusive, end exclusive: 23rd to 24th is 24 hours of Chicago,
    which is 24 hours of UTC starting at 05:00."""
    from app.routers.reports import _range_boundary

    start = _range_boundary("2026-08-23", CHICAGO)
    end = _range_boundary("2026-08-24", CHICAGO)
    assert (end - start) == timedelta(hours=24)
    assert local(start, CHICAGO).hour == 0
    assert local(end, CHICAGO).hour == 0


def test_a_timestamp_with_no_offset_is_read_as_district_time():
    """Whoever sent it was thinking in local terms; the server's own clock is
    not the answer to that question."""
    from app.routers.reports import _range_boundary

    naive = _range_boundary("2026-08-23T09:00:00", CHICAGO)
    assert naive.isoformat() == "2026-08-23T14:00:00+00:00"  # 9am Chicago


def test_a_bare_date_is_not_mistaken_for_a_naive_timestamp():
    """datetime.fromisoformat accepts "2026-08-23" and returns midnight naive,
    so trying the instant parser first read every date as midnight UTC."""
    from app.routers.reports import _range_boundary

    assert _range_boundary("2026-08-23", CHICAGO) == _range_boundary("2026-08-23T00:00:00", CHICAGO)
