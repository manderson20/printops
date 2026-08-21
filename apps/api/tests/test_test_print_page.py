"""The test page is drawn server-side, so its clock doesn't come from the
reader's browser the way the rest of PrintOps' timestamps do — the zone is
passed in instead. See app/printers/test_print.py."""

from datetime import UTC

from app.printers.test_print import _resolve_timezone


def test_named_zone_resolves():
    assert _resolve_timezone("America/Chicago").key == "America/Chicago"


def test_missing_zone_falls_back_to_utc():
    assert _resolve_timezone(None) is UTC
    assert _resolve_timezone("") is UTC


def test_unrecognised_zone_falls_back_instead_of_raising():
    # A stale or hand-edited value must never cost someone their test print.
    assert _resolve_timezone("Mars/Olympus_Mons") is UTC
    assert _resolve_timezone("../../etc/passwd") is UTC


def test_central_prints_its_own_abbreviation_and_offset():
    """The point of the change: a Central admin should read "CDT"/"CST" and
    a wall clock that matches the one on their desk, not UTC."""
    from datetime import datetime

    tz = _resolve_timezone("America/Chicago")
    summer = datetime(2026, 8, 21, 12, 0, tzinfo=UTC).astimezone(tz)
    winter = datetime(2026, 1, 21, 12, 0, tzinfo=UTC).astimezone(tz)

    assert summer.strftime("%H:%M %Z") == "07:00 CDT"
    assert winter.strftime("%H:%M %Z") == "06:00 CST"
