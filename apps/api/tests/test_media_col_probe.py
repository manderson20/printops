"""A printer is only called broken when it says so twice.

The Graphic Arts Kyocera answers a plain Validate-Job and drops the connection
on one carrying a nested media-col (2026-08-24), which stops its CUPS queue for
every job that names a page size. Acting on that means sending the printer's
jobs without a page size from then on, so a single timed-out probe must not be
enough to trigger it: a device that was merely busy, rebooting or briefly off
the network has to come out as "couldn't tell", not as broken.
"""

from unittest.mock import patch

from app.printers.media_col_probe import detect_media_col_broken, probe_uri

URI = "ipps://10.50.1.37:443/ipp/print"

ANSWERED = "successful-ok-ignored-or-substituted-attributes"


def _statuses(*sequence):
    """Feeds detect_media_col_broken one canned Validate-Job outcome per call,
    in order: control, media-col, then the control re-check."""
    answers = list(sequence)
    return patch(
        "app.printers.media_col_probe._validate_job_status",
        side_effect=lambda *_args, **_kwargs: answers.pop(0),
    )


def test_answers_plain_but_not_media_col_is_broken():
    with _statuses(ANSWERED, None, ANSWERED):
        assert detect_media_col_broken(URI) is True


def test_device_that_answers_both_is_fine():
    with _statuses(ANSWERED, "successful-ok"):
        assert detect_media_col_broken(URI) is False


def test_device_that_went_away_mid_probe_is_unknown():
    # Control answered, media-col didn't, and then the control stopped
    # answering too — the printer left, it did not object to media-col.
    with _statuses(ANSWERED, None, None):
        assert detect_media_col_broken(URI) is None


def test_unreachable_device_is_unknown_not_broken():
    with _statuses(None):
        assert detect_media_col_broken(URI) is None


def test_ipp_error_status_is_not_broken():
    # A device declining the attribute through the protocol is doing the
    # supported thing; CUPS retries without it and the queue survives. Only
    # silence stops a queue, so only silence is reported.
    with _statuses(ANSWERED, "client-error-attributes-or-values-not-supported"):
        assert detect_media_col_broken(URI) is False


def test_missing_ipptool_is_unknown():
    with patch("app.printers.media_col_probe.shutil.which", return_value=None):
        assert detect_media_col_broken(URI) is None


def test_probe_uri_uses_the_path_that_answered():
    assert probe_uri("10.50.1.37", 443, True, "/ipp/print") == URI
    assert probe_uri("10.10.3.36", 631, False, "/") == "ipp://10.10.3.36:631/"
    # The Konica bizhubs answer on "/" — a blank path must not silently become
    # /ipp/print for them, but a genuinely absent one still needs a default.
    assert probe_uri("10.0.0.1", 631, False, None) == "ipp://10.0.0.1:631/ipp/print"
