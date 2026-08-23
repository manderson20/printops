"""app/printers/job_control.py:cups_job_outcome — telling "cupsd says this job
is gone" apart from "cupsd didn't answer".

The reconciler writes a job off once it is two hours old and cupsd has no
record of it (app/printers/job_reconcile.py). That is only safe while a
"no record" verdict means cupsd actually said so: an IPP error is cupsd
declining to answer, and a job it declined to talk about may be printing.
"""

from app.printers import job_control
from app.printers.job_control import cups_job_outcome


def _plist(status: str, extra: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
<key>StatusCode</key><string>{status}</string>
{extra}
</dict>
</plist>
"""


def _answers(monkeypatch, output):
    monkeypatch.setattr(job_control, "_ipptool_plist", lambda queue, request: output)


def test_cups_saying_it_has_no_record_is_an_answer(monkeypatch):
    _answers(monkeypatch, _plist("client-error-not-found"))

    outcome = cups_job_outcome("printer-1", 4584)

    assert outcome is not None
    assert outcome.state is None


def test_an_authorization_error_is_not_an_answer(monkeypatch):
    """Reported as state=None, this used to become "cupsd has no record of
    this job" and cost the job its history two hours later."""
    _answers(monkeypatch, _plist("client-error-not-authorized"))

    assert cups_job_outcome("printer-1", 4584) is None


def test_a_transient_server_error_is_not_an_answer(monkeypatch):
    _answers(monkeypatch, _plist("server-error-busy"))

    assert cups_job_outcome("printer-1", 4584) is None


def test_a_cupsd_that_could_not_be_run_is_not_an_answer(monkeypatch):
    _answers(monkeypatch, None)

    assert cups_job_outcome("printer-1", 4584) is None


def test_a_completed_job_still_reports_what_it_printed(monkeypatch):
    _answers(
        monkeypatch,
        _plist(
            "successful-ok",
            """<key>job-state</key><integer>9</integer>
<key>job-media-sheets-completed</key><integer>12</integer>
<key>job-state-reasons</key><string>job-completed-successfully</string>
<key>sides</key><string>two-sided-long-edge</string>
<key>print-color-mode</key><string>monochrome</string>
<key>media</key><string>na_letter_8.5x11in</string>""",
        ),
    )

    outcome = cups_job_outcome("printer-1", 4584)

    assert outcome is not None
    assert outcome.state == job_control.JOB_STATE_COMPLETED
    assert outcome.page_count == 12
    assert outcome.color_mode == "monochrome"
    assert outcome.duplex is True
    assert outcome.paper_size == "na_letter_8.5x11in"
