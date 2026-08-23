"""app/printers/queue_recovery.py — starting a CUPS queue that cupsd stopped.

The failure these guard against: on 2026-08-21 the ES Veronica Copier was
taken away for a Service Call 0206. The `ipp` backend hit a broken pipe
mid Send-Document and eventually exited 4 (CUPS_BACKEND_STOP), so cupsd
stopped the queue. The copier came back serviced and idle — and the queue
stayed stopped for 31 hours with 19 teachers' jobs behind it, still
accepting more. Every signal PrintOps collected described the device, which
was genuinely fine; nothing described the queue.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.printer import Printer
from app.printers import queue_recovery, status
from app.printers.ipp_client import PrinterProbeError
from app.printers.queue_recovery import (
    QUEUE_PAUSED_REASON,
    RESUME_COOLDOWN,
    LocalQueueState,
    QueueResumeError,
)

PRINTER = "629d2c72-31eb-426d-8d91-1ab629a84ff7"
T0 = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)

STOPPED_OUTPUT = (
    "printer printops-629d2c72-31eb-426d-8d91-1ab629a84ff7 disabled since "
    "Fri Aug 21 16:26:34 2026 -\n\tUnable to add document to print job.\n"
)
IDLE_OUTPUT = (
    "printer printops-629d2c72-31eb-426d-8d91-1ab629a84ff7 is idle.  "
    "enabled since Sun Aug 23 00:09:11 2026\n"
)
PRINTING_OUTPUT = (
    "printer printops-629d2c72-31eb-426d-8d91-1ab629a84ff7 now printing "
    "printops-629d2c72-31eb-426d-8d91-1ab629a84ff7-4584.  enabled since "
    "Sun Aug 23 00:11:16 2026\n"
)
RELEASE_IDLE_OUTPUT = (
    "printer printops-release-629d2c72-31eb-426d-8d91-1ab629a84ff7 is idle.  "
    "enabled since Fri Aug 21 12:47:16 2026\n"
)
RELEASE_STOPPED_OUTPUT = (
    "printer printops-release-629d2c72-31eb-426d-8d91-1ab629a84ff7 disabled since "
    "Fri Aug 21 16:26:34 2026 -\n\tUnable to add document to print job.\n"
)


@pytest.fixture(autouse=True)
def _clean_state():
    queue_recovery.reset()
    yield
    queue_recovery.reset()


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _lpstat(monkeypatch, *, stdout="", returncode=0):
    calls = []

    def fake_run(argv, **kwargs):
        assert argv[0] == "lpstat"
        # The parsing reads cupsd's English wording, so the locale has to be
        # pinned or a non-English server would silently read every stopped
        # queue as running.
        assert kwargs["env"]["LC_ALL"] == "C"
        calls.append(argv[2])
        return _Completed(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(queue_recovery.subprocess, "run", fake_run)
    return calls


# ---- reading the queue's state ----


def test_a_stopped_queue_is_reported_with_cupsds_own_reason(monkeypatch):
    """The production signature, verbatim from the Veronica incident."""
    calls = _lpstat(monkeypatch, stdout=STOPPED_OUTPUT)
    state = queue_recovery.local_queue_state(PRINTER)
    assert state == LocalQueueState(stopped=True, message="Unable to add document to print job.")
    # Both queues in one call — the release queue delivers to the same
    # device and can be stopped by the same failure.
    assert calls == [f"printops-{PRINTER},printops-release-{PRINTER}"]


def test_a_stopped_release_queue_is_caught_too(monkeypatch):
    """A stopped release queue fails PIN releases at the panel silently,
    which is harder to notice than a client queue that visibly backs up."""
    _lpstat(monkeypatch, stdout=IDLE_OUTPUT + RELEASE_STOPPED_OUTPUT)
    state = queue_recovery.local_queue_state(PRINTER)
    assert state.stopped is True
    assert state.message == "Unable to add document to print job."


def test_both_queues_running_reads_as_healthy(monkeypatch):
    _lpstat(monkeypatch, stdout=IDLE_OUTPUT + RELEASE_IDLE_OUTPUT)
    assert queue_recovery.local_queue_state(PRINTER).stopped is False


def test_a_printer_with_no_release_queue_is_still_checked(monkeypatch):
    """lpstat fails the whole call if any name is unknown, and a virtual
    Follow-Me printer has no release queue by design. Without the fallback
    every virtual printer would read "couldn't tell" forever."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[2])
        if "," in argv[2]:
            return _Completed(returncode=1, stderr="lpstat: Unknown destination")
        return _Completed(stdout=STOPPED_OUTPUT)

    monkeypatch.setattr(queue_recovery.subprocess, "run", fake_run)

    assert queue_recovery.local_queue_state(PRINTER).stopped is True
    assert calls == [
        f"printops-{PRINTER},printops-release-{PRINTER}",
        f"printops-{PRINTER}",
    ]


@pytest.mark.parametrize("output", [IDLE_OUTPUT, PRINTING_OUTPUT])
def test_a_running_queue_is_not_reported_as_stopped(monkeypatch, output):
    """Both wordings cupsd uses for a healthy queue. "now printing" is the
    one a busy printer shows, and reading it as stopped would have PrintOps
    resuming queues all day."""
    _lpstat(monkeypatch, stdout=output)
    assert queue_recovery.local_queue_state(PRINTER).stopped is False


def test_an_unanswered_lpstat_is_not_mistaken_for_a_healthy_queue(monkeypatch):
    """None is deliberately distinct from "running": a queue lpstat didn't
    answer for is not evidence of anything, and must not be written off."""
    _lpstat(monkeypatch, returncode=1)
    assert queue_recovery.local_queue_state(PRINTER) is None


def test_a_printer_with_no_queue_at_all_reads_as_unknown(monkeypatch):
    _lpstat(monkeypatch, stdout="")
    assert queue_recovery.local_queue_state(PRINTER) is None


def test_lpstat_blowing_up_is_not_an_exception_the_status_poll_has_to_catch(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no lpstat here")

    monkeypatch.setattr(queue_recovery.subprocess, "run", boom)
    assert queue_recovery.local_queue_state(PRINTER) is None


# ---- the cooldown ----


def test_a_queue_never_resumed_is_due_immediately():
    assert queue_recovery.resume_due(PRINTER, now=T0) is True


def test_a_queue_resumed_moments_ago_waits(monkeypatch):
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    queue_recovery.resume_queue(PRINTER, now=T0)
    assert queue_recovery.resume_due(PRINTER, now=T0 + timedelta(minutes=1)) is False
    assert queue_recovery.resume_due(PRINTER, now=T0 + RESUME_COOLDOWN) is True


def test_a_resume_that_does_not_hold_backs_off_further(monkeypatch):
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    queue_recovery.resume_queue(PRINTER, now=T0)
    queue_recovery.note_still_stopped(PRINTER)
    # One failure doubles the wait, so the plain cooldown is no longer enough.
    assert queue_recovery.resume_due(PRINTER, now=T0 + RESUME_COOLDOWN) is False
    assert queue_recovery.resume_due(PRINTER, now=T0 + 2 * RESUME_COOLDOWN) is True


def test_a_stopped_queue_counts_once_per_resume_not_once_per_poll(monkeypatch):
    """The 60s poll sees the same stopped queue over and over. Counting each
    sighting would run the backoff to its cap inside twenty minutes and make
    the doubling meaningless."""
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    queue_recovery.resume_queue(PRINTER, now=T0)
    for _ in range(10):
        queue_recovery.note_still_stopped(PRINTER)
    assert queue_recovery.resume_due(PRINTER, now=T0 + 2 * RESUME_COOLDOWN) is True


def test_a_queue_stopped_since_before_this_process_started_has_not_failed_anything():
    """Nothing was attempted, so there is no verdict to record — otherwise a
    restart would hand every long-stopped queue an undeserved backoff."""
    queue_recovery.note_still_stopped(PRINTER)
    assert queue_recovery.resume_due(PRINTER, now=T0) is True


def test_backoff_is_capped_so_a_queue_is_never_abandoned(monkeypatch):
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    for _ in range(50):
        queue_recovery.resume_queue(PRINTER, now=T0)
        queue_recovery.note_still_stopped(PRINTER)
    assert queue_recovery.resume_due(PRINTER, now=T0 + queue_recovery.MAX_RESUME_BACKOFF) is True


# ---- the backoff, when the device comes back ----


def _fail_into_the_cap(monkeypatch):
    """Seven resumes that didn't hold — the state a flapping printer reaches
    within an hour, and the state that used to leave a serviced one dead."""
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    for _ in range(7):
        queue_recovery.resume_queue(PRINTER, now=T0)
        queue_recovery.note_still_stopped(PRINTER)


def test_a_printer_back_from_service_does_not_wait_out_its_backoff(monkeypatch):
    """The 31-hour outage in a slower form: the backoff was earned while the
    copier was unplugged, and a clock alone would keep the queue off for up to
    four hours after it came back."""
    _fail_into_the_cap(monkeypatch)
    queue_recovery.note_device_away(PRINTER)
    assert queue_recovery.resume_due(PRINTER, now=T0 + timedelta(hours=1)) is False

    back = T0 + timedelta(hours=1)
    stayed = back + queue_recovery.HEALTHY_RESET_PERIOD
    queue_recovery.note_device_back(PRINTER, now=back)
    queue_recovery.note_device_back(PRINTER, now=stayed)

    assert queue_recovery.resume_due(PRINTER, now=stayed) is True


def test_a_device_that_flaps_back_for_a_moment_does_not_reset_anything(monkeypatch):
    """One healthy reading is not a return. Resetting on it would hand a
    flapping printer a resume per cycle, which is what the doubling exists to
    prevent."""
    _fail_into_the_cap(monkeypatch)
    queue_recovery.note_device_away(PRINTER)

    back = T0 + timedelta(hours=1)
    queue_recovery.note_device_back(PRINTER, now=back)
    queue_recovery.note_device_back(PRINTER, now=back + timedelta(minutes=1))
    # ...and away it goes again before the period is up.
    queue_recovery.note_device_away(PRINTER)
    queue_recovery.note_device_back(PRINTER, now=back + timedelta(minutes=2))

    assert queue_recovery.resume_due(PRINTER, now=back + timedelta(minutes=2)) is False


def test_a_device_that_never_went_away_keeps_its_backoff(monkeypatch):
    """A device insisting it is online while every resume fails is exactly
    what the backoff is for. Healthy readings from it are not news."""
    _fail_into_the_cap(monkeypatch)
    for minute in range(30):
        queue_recovery.note_device_back(PRINTER, now=T0 + timedelta(minutes=minute))

    assert queue_recovery.resume_due(PRINTER, now=T0 + timedelta(minutes=30)) is False


def test_a_printer_with_no_recovery_in_progress_is_not_invented(monkeypatch):
    """Both notes are called on every poll of a stopped queue, including for
    printers this process has never attempted anything for."""
    queue_recovery.note_device_away(PRINTER)
    queue_recovery.note_device_back(PRINTER, now=T0)

    assert queue_recovery.resume_due(PRINTER, now=T0) is True


def test_a_failed_resume_is_reported_rather_than_swallowed(monkeypatch):
    monkeypatch.setattr(
        queue_recovery.subprocess,
        "run",
        lambda *_a, **_k: _Completed(returncode=1, stderr="No CUPS queues found"),
    )
    with pytest.raises(QueueResumeError, match="No CUPS queues found"):
        queue_recovery.resume_queue(PRINTER, now=T0)


# ---- what the status poll does with it ----


def _printer(**kwargs):
    kwargs.setdefault("name", "ES Veronica Copier")
    kwargs.setdefault("ip_address", "10.10.3.36")
    printer = Printer(**kwargs)
    printer.id = PRINTER
    return printer


def _queue(monkeypatch, state):
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: False)
    monkeypatch.setattr(queue_recovery, "local_queue_state", lambda _pid: state)


def _record_resumes(monkeypatch):
    resumed = []
    monkeypatch.setattr(queue_recovery, "resume_queue", lambda pid, **_k: resumed.append(pid))
    return resumed


STOPPED = LocalQueueState(stopped=True, message="Unable to add document to print job.")


@pytest.mark.asyncio
async def test_a_serviced_printer_gets_its_queue_started_again(monkeypatch):
    """The whole point: the copier is back, idle and accepting jobs, and
    nobody should have to run cupsenable by hand for the backlog to move."""
    _queue(monkeypatch, STOPPED)
    resumed = _record_resumes(monkeypatch)
    printer = _printer(status="online")

    handled = await status._apply_queue_recovery(printer)

    assert handled is True
    assert resumed == [PRINTER]
    # The fault is over. Leaving it reading "error" would flag something
    # nobody can act on.
    assert printer.status == "online"
    assert "started again automatically" in printer.status_message
    # No reason keyword: the UI paints those as red badges, and a red badge
    # on a printer that is working again is an error nobody can act on.
    assert printer.status_reasons is None


@pytest.mark.asyncio
async def test_a_printer_that_is_still_broken_keeps_its_own_diagnosis(monkeypatch):
    """Resuming now would just hand cupsd one more job to fail. The device's
    own message is the better thing to show, so it survives — with the
    paused queue recorded alongside it."""
    _queue(monkeypatch, STOPPED)
    resumed = _record_resumes(monkeypatch)
    printer = _printer(status="offline", status_message="Timeout connecting to IPP")

    handled = await status._apply_queue_recovery(printer)

    assert handled is False
    assert resumed == []
    assert printer.status == "offline"
    assert printer.status_message == "Timeout connecting to IPP"
    assert QUEUE_PAUSED_REASON in printer.status_reasons


@pytest.mark.asyncio
async def test_the_stall_clock_is_restarted_when_a_queue_is_found_stopped(monkeypatch):
    """How long the head job sat on a stopped queue only measures how long
    the queue was off. Left standing, the stall detector would fire the
    moment the queue restarts, against a job that is printing fine."""
    from app.printers import queue_stall
    from app.printers.job_control import QueueSnapshot

    queue_stall.reset()
    long_ago = T0 - timedelta(hours=4)
    queue_stall.observe(PRINTER, QueueSnapshot(depth=19, head_job="4584"), now=long_ago)

    _queue(monkeypatch, STOPPED)
    _record_resumes(monkeypatch)
    await status._apply_queue_recovery(_printer(status="online"))

    # Same head job, four hours on — and no stall, because the clock restarted.
    assert queue_stall.observe(PRINTER, QueueSnapshot(depth=19, head_job="4584"), now=T0) is None
    queue_stall.reset()


@pytest.mark.asyncio
async def test_a_running_queue_is_left_alone(monkeypatch):
    _queue(monkeypatch, LocalQueueState(stopped=False))
    resumed = _record_resumes(monkeypatch)
    printer = _printer(status="online")

    assert await status._apply_queue_recovery(printer) is False
    assert resumed == []
    assert printer.status_reasons is None


@pytest.mark.asyncio
async def test_nothing_is_resumed_while_cupsd_is_short_on_slots(monkeypatch):
    """Same rule as the stall check: a scheduler with no room left is not the
    moment to spend a slot on maintenance."""
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: True)
    resumed = _record_resumes(monkeypatch)

    assert await status._apply_queue_recovery(_printer(status="online")) is False
    assert resumed == []


@pytest.mark.asyncio
async def test_a_person_checking_the_status_is_never_made_to_wait(monkeypatch):
    """The admin has just walked the printer back online and pressed Check
    Status. A cooldown they can't see would look exactly like the bug."""
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery, "resume_due", lambda _pid: False)
    resumed = _record_resumes(monkeypatch)

    await status._apply_queue_recovery(_printer(status="online"), manual=True)
    assert resumed == [PRINTER]


@pytest.mark.asyncio
async def test_a_queue_is_not_resumed_on_a_probe_that_did_not_answer(monkeypatch):
    """Since the offline debounce, `status` can still read "online" for one
    missed probe after the device stopped answering. Resuming a queue on the
    strength of that stale value hands cupsd a job for a printer that has
    just failed to respond — the exact case the "only when the device reads
    healthy" rule exists to prevent."""
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery, "resume_due", lambda _pid: True)
    resumed = _record_resumes(monkeypatch)
    printer = _printer(status="online")

    handled = await status._apply_queue_recovery(printer, device_answered=False)

    assert handled is False
    assert resumed == []
    assert QUEUE_PAUSED_REASON in (printer.status_reasons or [])


@pytest.mark.asyncio
async def test_not_even_a_manual_check_resumes_on_a_probe_that_did_not_answer(monkeypatch):
    """Pressing Check Status skips the cooldown, not the evidence."""
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery, "resume_due", lambda _pid: True)
    resumed = _record_resumes(monkeypatch)

    await status._apply_queue_recovery(
        _printer(status="online"), manual=True, device_answered=False
    )

    assert resumed == []


@pytest.mark.asyncio
async def test_a_missed_probe_leaves_the_queue_alone_end_to_end(monkeypatch):
    """At the level the 60-second poll works at: the queue is stopped, the
    printer row still says online because only one probe has been missed, and
    that probe raised. Nothing should be resumed and no stall clock started."""
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery, "resume_due", lambda _pid: True)
    resumed = _record_resumes(monkeypatch)
    stalls = []
    monkeypatch.setattr(status, "_apply_queue_stall", lambda printer: stalls.append(printer))

    async def _unreachable(*_a, **_k):
        raise PrinterProbeError("Could not reach an IPP printer at 10.10.3.36:631: timed out")

    monkeypatch.setattr(status, "probe_printer_state", _unreachable)
    printer = _printer(status="online")

    await status.refresh_printer_status(printer)

    assert resumed == []
    assert stalls == []
    # One miss: still shown as online, and the failure is counted toward the
    # second one that would change that.
    assert printer.status_probe_failures == 1


@pytest.mark.asyncio
async def test_an_automatic_check_on_cooldown_reports_without_resuming(monkeypatch):
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery, "resume_due", lambda _pid: False)
    resumed = _record_resumes(monkeypatch)
    printer = _printer(status="online")

    assert await status._apply_queue_recovery(printer) is True
    assert resumed == []
    assert printer.status == "error"
    assert QUEUE_PAUSED_REASON in printer.status_reasons
    assert "piling up instead of printing" in printer.status_message


@pytest.mark.asyncio
async def test_a_resume_that_fails_is_surfaced_not_raised(monkeypatch):
    """A status poll that raises would stop every other printer in the cycle
    from being checked."""
    _queue(monkeypatch, STOPPED)

    def boom(_pid, **_k):
        raise QueueResumeError("cupsenable: not found")

    monkeypatch.setattr(queue_recovery, "resume_queue", boom)
    printer = _printer(status="online")

    assert await status._apply_queue_recovery(printer) is True
    assert printer.status == "error"


@pytest.mark.asyncio
async def test_a_stopped_queue_is_not_also_reported_as_a_mystery_stall(monkeypatch):
    """The stall detector's advice — check the port, TLS and IPP path — is
    precisely the wrong place to send someone whose printer was away being
    serviced. The specific diagnosis wins."""
    _queue(monkeypatch, STOPPED)
    _record_resumes(monkeypatch)
    stalls = []
    monkeypatch.setattr(status, "_apply_queue_stall", lambda printer: stalls.append(printer))
    monkeypatch.setattr(
        status,
        "probe_printer_state",
        _async_state(printer_state=3, state_reasons=["none"]),
    )

    printer = _printer(status="online", port=631, use_tls=False)
    await status.refresh_printer_status(printer)

    assert stalls == []
    assert "started again automatically" in printer.status_message


def _async_state(**kwargs):
    from app.printers.ipp_client import PrinterStateResult

    async def _probe(*_a, **_k):
        return PrinterStateResult(state_message=None, **kwargs)

    return _probe


@pytest.mark.asyncio
async def test_a_serviced_printer_is_resumed_once_it_has_come_back_and_stayed(monkeypatch):
    """End to end, at the level the 60-second poll works at: the queue is
    stopped, the copier was away long enough to run the backoff up to its cap,
    and it is now back. It should print, not wait out a timer."""
    _queue(monkeypatch, STOPPED)
    monkeypatch.setattr(queue_recovery.subprocess, "run", lambda *_a, **_k: _Completed())
    for _ in range(7):
        queue_recovery.resume_queue(PRINTER)
        queue_recovery.note_still_stopped(PRINTER)
    resumed = _record_resumes(monkeypatch)
    # Real polls, so real elapsed time — the reset period is what's under test
    # here, not how long it is.
    monkeypatch.setattr(queue_recovery, "HEALTHY_RESET_PERIOD", timedelta(0))

    # While it is away: checked every cycle, never resumed. The queue keeps
    # accepting, so its users' jobs queue up behind it rather than failing.
    away = _printer(status="offline", status_message="Timeout connecting to IPP")
    for _ in range(5):
        assert await status._apply_queue_recovery(away) is False
    assert resumed == []
    assert QUEUE_PAUSED_REASON in away.status_reasons

    # It comes back. The first healthy poll starts the clock, the next one
    # clears the backoff and the queue starts.
    back = _printer(status="online")
    assert await status._apply_queue_recovery(back) is True
    assert resumed == []

    back = _printer(status="online")
    assert await status._apply_queue_recovery(back) is True
    assert resumed == [PRINTER]
    assert back.status == "online"
    assert "started again automatically" in back.status_message
