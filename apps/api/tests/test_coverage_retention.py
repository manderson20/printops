"""Whether CUPS is keeping documents long enough to measure them.

Coverage depends on a spool file outliving the job. CUPS deletes job data as
soon as it prints unless PreserveJobFiles says otherwise, and its default is
off — so an installation that never set it gets no measurements at all, as a
silent absence rather than an error.

The machine this was written on happened to have retention configured, which is
exactly why it needed a test: a feature that works where it was built and
nowhere else looks finished.
"""

import pytest

from app.coverage.retention import (
    MINIMUM_SECONDS,
    configured_retention_seconds,
    warn_if_documents_are_not_retained,
)


def conf(tmp_path, body: str):
    path = tmp_path / "cupsd.conf"
    path.write_text(body)
    return path


@pytest.mark.parametrize(
    ("body", "expected", "because"),
    [
        ("LogLevel warn\n", 0, "absent means CUPS's own default, which is off"),
        ("PreserveJobFiles No\n", 0, "explicitly off"),
        ("PreserveJobFiles off\n", 0, "the other spelling of off"),
        ("PreserveJobFiles 259200\n", 259200, "three days"),
        ("  preservejobfiles   3600\n", 3600, "directives are case-insensitive"),
        ("PreserveJobFiles Yes\n", 10**9, "kept indefinitely"),
    ],
)
def test_reading_the_directive(tmp_path, body, expected, because):
    assert configured_retention_seconds(conf(tmp_path, body)) == expected, because


def test_an_unreadable_file_is_not_the_same_as_unconfigured(tmp_path):
    """Reporting "retention is off" for a file we could not open would send an
    admin to change a setting that may already be correct."""
    assert configured_retention_seconds(tmp_path / "does-not-exist.conf") is None


def test_retention_that_is_off_warns_with_the_fix_in_it(tmp_path):
    message = warn_if_documents_are_not_retained(conf(tmp_path, "PreserveJobFiles No\n"))
    assert message is not None
    assert "PreserveJobFiles" in message
    assert "259200" in message, "the warning should say what to set, not just that it is wrong"


def test_retention_shorter_than_the_settle_window_warns(tmp_path):
    """A minute of retention is worse than none: it looks configured."""
    message = warn_if_documents_are_not_retained(conf(tmp_path, "PreserveJobFiles 60\n"))
    assert message is not None
    assert str(MINIMUM_SECONDS) in message


def test_adequate_retention_says_nothing(tmp_path):
    assert warn_if_documents_are_not_retained(conf(tmp_path, "PreserveJobFiles 259200\n")) is None
