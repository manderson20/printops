from unittest.mock import MagicMock, patch

import pytest

from app.printers.release import ReleaseError, parse_job_options, submit_released_job


def test_parse_job_options_splits_key_value_tokens():
    assert parse_job_options("sides=one-sided media=na_letter_8.5x11in") == [
        "sides=one-sided",
        "media=na_letter_8.5x11in",
    ]


def test_parse_job_options_drops_malformed_tokens():
    assert parse_job_options("sides=one-sided garbage") == ["sides=one-sided"]


def test_parse_job_options_none_returns_empty():
    assert parse_job_options(None) == []


def test_parse_job_options_empty_string_returns_empty():
    assert parse_job_options("") == []


def _fake_run(returncode=0, stdout="request id is printops-release-x-1", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_submit_released_job_builds_expected_argv():
    with patch("app.printers.release.subprocess.run", return_value=_fake_run()) as mock_run:
        submit_released_job(
            printer_id="abc123",
            held_file_path="/var/spool/printops-held/job1",
            document_name="Report.pdf",
            copy_count=2,
            held_job_options="sides=two-sided-long-edge media=na_letter_8.5x11in",
        )

    argv = mock_run.call_args[0][0]
    assert argv[:3] == ["lp", "-d", "printops-release-abc123"]
    assert "-n" in argv and argv[argv.index("-n") + 1] == "2"
    assert "-t" in argv and argv[argv.index("-t") + 1] == "Report.pdf"
    assert "-o" in argv
    assert "sides=two-sided-long-edge" in argv
    assert "media=na_letter_8.5x11in" in argv
    assert argv[-1] == "/var/spool/printops-held/job1"


def test_submit_released_job_omits_copies_and_title_when_absent():
    with patch("app.printers.release.subprocess.run", return_value=_fake_run()) as mock_run:
        submit_released_job(
            printer_id="abc123",
            held_file_path="/var/spool/printops-held/job1",
            document_name=None,
            copy_count=None,
            held_job_options=None,
        )
    argv = mock_run.call_args[0][0]
    assert "-n" not in argv
    assert "-t" not in argv


def test_submit_released_job_raises_on_lp_failure():
    with patch(
        "app.printers.release.subprocess.run",
        return_value=_fake_run(returncode=1, stderr="lp: some error"),
    ):
        with pytest.raises(ReleaseError, match="some error"):
            submit_released_job("abc123", "/tmp/x", None, None, None)


def test_submit_released_job_translates_missing_queue_error():
    with patch(
        "app.printers.release.subprocess.run",
        return_value=_fake_run(returncode=1, stderr="lp: The printer or class does not exist."),
    ):
        with pytest.raises(ReleaseError, match="sync_release_queue.sh"):
            submit_released_job("abc123", "/tmp/x", None, None, None)


def test_submit_released_job_translates_missing_lp_binary():
    with patch("app.printers.release.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(ReleaseError, match="isn't available"):
            submit_released_job("abc123", "/tmp/x", None, None, None)
