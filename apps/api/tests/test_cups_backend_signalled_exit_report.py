"""infra/cups/backends/printops — reporting a job that cupsd ended by signal.

A `jobs` row is written the moment delivery starts and only leaves
status="forwarding" when this script reports back. cupsd SIGTERMs a backend
whenever it cancels, holds or restarts a job — the restart path fires on any
`lpadmin` against the queue, which is to say on every queue resync PrintOps
itself runs — and the handler that reaps the child used to exit without saying
anything. The row then said "printing" forever. 107 of them had accumulated by
August, from July onwards, at 2-3% of every job on the server.

Same in-process approach as test_cups_backend_child_reaping.py: the handler is
called directly rather than delivered as a real signal, which would be caught
by pytest's own machinery before any assertion could run.
"""

import importlib.util
import os
import signal
import sys
import urllib.error
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "infra" / "cups" / "backends" / "printops"

JOB_ID = "0d1f37f0-5b1c-4bca-9efb-61cc50914c15"


@pytest.fixture
def backend_module():
    loader = SourceFileLoader("printops_cups_backend_signal_report", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def calls(backend_module, monkeypatch):
    """Captures what the script would have sent to the API."""
    recorded = []

    def fake_api_request(token, method, path, body=None, timeout=10):
        recorded.append({"method": method, "path": path, "body": body, "timeout": timeout})
        return {}

    monkeypatch.setattr(backend_module, "api_request", fake_api_request)
    return recorded


@pytest.fixture
def armed(backend_module, calls):
    """A backend mid-delivery: job logged, token loaded, handler installed."""
    previous = signal.getsignal(signal.SIGTERM)
    backend_module._JOB_RECORD_ID = JOB_ID
    backend_module._API_TOKEN = "token"
    backend_module._REPORTED = False
    backend_module._install_child_signal_forwarding()
    installed = signal.getsignal(signal.SIGTERM)
    try:
        yield installed
    finally:
        signal.signal(signal.SIGTERM, previous)
        backend_module._CHILD_PROC = None
        backend_module._JOB_RECORD_ID = None
        backend_module._API_TOKEN = None
        backend_module._REPORTED = False


def test_a_signalled_job_is_recorded_instead_of_left_printing_forever(armed, calls):
    with pytest.raises(SystemExit) as exc:
        armed(signal.SIGTERM, None)

    assert exc.value.code == 128 + signal.SIGTERM
    assert len(calls) == 1
    assert calls[0]["method"] == "PATCH"
    assert calls[0]["path"] == f"/api/v1/jobs/{JOB_ID}"
    assert calls[0]["body"]["status"] == "cancelled"
    assert "SIGTERM" in calls[0]["body"]["error_message"]


def test_a_signalled_job_is_not_recorded_as_a_failure(armed, calls):
    """Nothing failed. Either this attempt was ended deliberately or it is
    about to be restarted, in which case the next attempt gets its own row and
    carries what was printed — a failure here is one nobody can act on."""
    with pytest.raises(SystemExit):
        armed(signal.SIGTERM, None)

    assert calls[0]["body"]["status"] != "failed"


def test_the_report_waits_for_the_child_to_die(backend_module, armed, calls):
    """Reaping the leaked `ipp` backend is what stops a cancelled job
    hammering the device (see test_cups_backend_child_reaping.py). The status
    line is best-effort; that is not. It must not be able to delay it."""
    child = backend_module._run_real_backend(
        [sys.executable, "-c", "import time; time.sleep(120)"], dict(os.environ)
    )
    seen_alive = []

    def fake_api_request(*_a, **_k):
        seen_alive.append(child.poll() is None)
        return {}

    backend_module.api_request = fake_api_request

    with pytest.raises(SystemExit):
        armed(signal.SIGTERM, None)

    assert seen_alive == [False], "reported the job while the child was still running"


def test_a_signal_before_the_job_exists_reports_nothing(backend_module, armed, calls):
    """Signals can arrive during the API-bookkeeping phase, before there is a
    job to report on. Inventing a PATCH there would 404 at best."""
    backend_module._JOB_RECORD_ID = None

    with pytest.raises(SystemExit):
        armed(signal.SIGTERM, None)

    assert calls == []


def test_a_second_signal_does_not_report_the_job_twice(armed, calls):
    """cupsd is entitled to send SIGTERM again while the first report is still
    in flight."""
    for _ in range(3):
        with pytest.raises(SystemExit):
            armed(signal.SIGTERM, None)

    assert len(calls) == 1


def test_an_api_that_is_down_does_not_stop_the_backend_exiting(backend_module, armed):
    """The report is best-effort for the same reason the completion report is:
    a print that already happened must not be undone by a status update that
    didn't. app/printers/job_reconcile.py resolves whatever this misses."""

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    backend_module.api_request = boom

    with pytest.raises(SystemExit) as exc:
        armed(signal.SIGTERM, None)

    assert exc.value.code == 128 + signal.SIGTERM


def test_the_report_cannot_outlast_cupsds_patience(backend_module, armed, calls):
    """cupsd follows SIGTERM with SIGKILL. A report that doesn't fit in that
    gap is not worth delaying the teardown for, so it gets a much shorter
    timeout than an ordinary API call."""
    with pytest.raises(SystemExit):
        armed(signal.SIGTERM, None)

    assert calls[0]["timeout"] == backend_module.SIGNAL_REPORT_TIMEOUT_SECONDS
    assert backend_module.SIGNAL_REPORT_TIMEOUT_SECONDS < backend_module.CHILD_TERM_GRACE_SECONDS
