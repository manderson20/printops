"""app/printers/crash_guard.py — a job that takes its printer down is not sent
to it forever.

The incident: an office LaserJet 600 M601 crashed on one PDF on 2026-09-11, and
CUPS re-sent that PDF after every power-cycle for three days. PrintOps never saw
the printer answer in between. These tests drive the status poll through that
sequence with the probe, cupsd and the pause/hold scripts stubbed out.
"""

import plistlib
from datetime import UTC, datetime

import pytest

from app.models.printer import Printer
from app.printers import crash_guard, job_control, queue_recovery, status
from app.printers.ipp_client import PrinterProbeError, PrinterStateResult
from app.printers.job_control import HeldCupsJob
from app.printers.queue_recovery import LocalQueueState, QueuePauseError

PRINTER = "9c2d5c5f-fddf-4a98-a0ea-b0bd0329efdc"
POISON = 12355
INNOCENT = 12400


@pytest.fixture(autouse=True)
def _clean_state():
    crash_guard.reset()
    queue_recovery.reset()
    yield
    crash_guard.reset()
    queue_recovery.reset()


# --- the bookkeeping


def test_one_loss_mid_job_holds_nothing():
    """A printer switched off part-way through a job is ordinary, and the job
    it was printing is innocent."""
    assert crash_guard.note_lost_during(PRINTER, (POISON,)) == []


def test_the_same_job_in_flight_at_a_second_loss_is_held():
    crash_guard.note_lost_during(PRINTER, (POISON,))
    crash_guard.note_answered(PRINTER)
    assert crash_guard.note_lost_during(PRINTER, (POISON,)) == [POISON]


def test_losses_without_an_answer_in_between_are_one_outage():
    """A queue re-enabled by hand while the printer is still away sends the job
    again into the same outage. That is not a second piece of evidence."""
    crash_guard.note_lost_during(PRINTER, (POISON,))
    assert crash_guard.note_lost_during(PRINTER, (POISON,)) == []
    assert crash_guard.strikes(PRINTER, POISON) == 1


def test_two_outages_during_different_jobs_do_not_add_up():
    crash_guard.note_lost_during(PRINTER, (POISON,))
    crash_guard.note_answered(PRINTER)
    crash_guard.note_lost_during(PRINTER, (INNOCENT,))
    crash_guard.note_answered(PRINTER)
    assert crash_guard.note_lost_during(PRINTER, (POISON,)) == []


def test_printers_are_counted_separately():
    crash_guard.note_lost_during(PRINTER, (POISON,))
    assert crash_guard.note_lost_during("another-printer", (POISON,)) == []


def _suspect(cups_job_id=POISON, document_name="8th Grade Missing Work", owner="a teacher"):
    return crash_guard.HeldSuspect(
        cups_job_id=cups_job_id,
        document_name=document_name,
        owner=owner,
        held_at=datetime(2026, 9, 14, 15, 0, tzinfo=UTC),
    )


def test_a_released_job_is_held_again_at_the_next_loss():
    """Releasing it is a judgement the printer can still overrule — without
    waiting for two more crashes."""
    crash_guard.note_lost_during(PRINTER, (POISON,))
    crash_guard.note_answered(PRINTER)
    crash_guard.note_lost_during(PRINTER, (POISON,))
    crash_guard.record_held(PRINTER, _suspect())

    crash_guard.forget_held(PRINTER, POISON, released=True)
    crash_guard.note_answered(PRINTER)

    assert crash_guard.held(PRINTER) == []
    assert crash_guard.note_lost_during(PRINTER, (POISON,)) == [POISON]


def test_releasing_a_hold_printops_did_not_place_leaves_no_strike():
    """Held by hand, say. Nothing about that job was ever observed, so one
    ordinary outage while it prints must not hold it."""
    crash_guard.forget_held(PRINTER, INNOCENT, released=True)
    assert crash_guard.strikes(PRINTER, INNOCENT) == 0


def test_a_cancelled_job_leaves_nothing_behind():
    crash_guard.note_lost_during(PRINTER, (POISON,))
    crash_guard.record_held(PRINTER, _suspect())

    crash_guard.forget_held(PRINTER, POISON, released=False)

    assert crash_guard.held(PRINTER) == []
    assert crash_guard.strikes(PRINTER, POISON) == 0


def test_jobs_cupsd_no_longer_holds_are_forgotten():
    crash_guard.record_held(PRINTER, _suspect(POISON))
    crash_guard.record_held(PRINTER, _suspect(INNOCENT))

    crash_guard.forget_held_except(PRINTER, {INNOCENT})

    assert [s.cups_job_id for s in crash_guard.held(PRINTER)] == [INNOCENT]


def test_the_reason_names_the_document_and_who_sent_it():
    reason = crash_guard.held_reason([_suspect()])
    assert '"8th Grade Missing Work"' in reason
    assert "from a teacher" in reason
    assert "rest of the queue is printing" in reason


def test_the_reason_still_reads_without_a_name():
    reason = crash_guard.held_reason([_suspect(document_name=None, owner=None)])
    assert f"job {POISON}" in reason


# --- reading what cupsd is sending


def test_now_printing_lines_give_the_jobs_in_flight_on_both_queues():
    output = (
        f"printer printops-{PRINTER} now printing printops-{PRINTER}-12348.  "
        "enabled since Fri Sep 11 18:54:21 2026\n"
        f"printer printops-release-{PRINTER} now printing printops-release-{PRINTER}-12355.  "
        "enabled since Mon Sep 14 00:52:12 2026\n"
    )
    assert queue_recovery._parse(output).printing == (12348, 12355)


def test_an_idle_or_stopped_queue_has_nothing_in_flight():
    output = (
        f"printer printops-{PRINTER} is idle.  enabled since Mon Sep 14 16:02:06 2026\n"
        f"printer printops-release-{PRINTER} disabled since Mon Sep 14 16:05:01 2026 -\n"
        "\tgstoraster filter failed.\n"
    )
    state = queue_recovery._parse(output)
    assert state.printing == ()
    assert state.stopped is True


def _plist(status_code, jobs=()):
    groups = [{"attributes-charset": "utf-8"}, *jobs]
    return plistlib.dumps(
        {"Tests": [{"StatusCode": status_code, "ResponseAttributes": groups}]}
    ).decode()


def test_held_jobs_are_read_from_both_queues_and_only_held_ones_count(monkeypatch):
    answers = {
        f"printops-{PRINTER}": _plist(
            "successful-ok", [{"job-id": INNOCENT, "job-state": 3, "job-name": "waiting"}]
        ),
        f"printops-release-{PRINTER}": _plist(
            "successful-ok",
            [
                {
                    "job-id": POISON,
                    "job-uuid": "urn:uuid:f5599466-e231-351d-6c51-616f08472153",
                    "job-state": 4,
                    "job-name": "8th Grade Missing Work",
                    "job-originating-user-name": "a teacher",
                    "job-k-octets": 427,
                    "time-at-creation": 1789153070,
                }
            ],
        ),
    }
    monkeypatch.setattr(job_control, "_ipptool_plist", lambda queue, _request: answers[queue])

    [job] = job_control.held_cups_jobs(PRINTER)

    assert job.cups_job_id == POISON
    assert job.queue == "release"
    assert job.document_name == "8th Grade Missing Work"
    assert job.owner == "a teacher"
    assert job.size_bytes == 427 * 1024
    assert job.job_uuid == "urn:uuid:f5599466-e231-351d-6c51-616f08472153"
    assert job.submitted_at == datetime(2026, 9, 11, 18, 57, 50, tzinfo=UTC)


def test_a_printer_without_a_release_queue_is_not_an_error(monkeypatch):
    answers = {
        f"printops-{PRINTER}": _plist("successful-ok"),
        f"printops-release-{PRINTER}": _plist("client-error-not-found"),
    }
    monkeypatch.setattr(job_control, "_ipptool_plist", lambda queue, _request: answers[queue])
    assert job_control.held_cups_jobs(PRINTER) == []


@pytest.mark.parametrize("answer", [None, "server-error-internal-error", "not a plist"])
def test_an_unanswered_question_is_not_an_empty_list(monkeypatch, answer):
    """Same distinction as queue_snapshot: cupsd not answering is not evidence
    that nothing is held."""
    if answer not in (None, "not a plist"):
        answer = _plist(answer)
    monkeypatch.setattr(job_control, "_ipptool_plist", lambda _queue, _request: answer)
    assert job_control.held_cups_jobs(PRINTER) is None


# --- the status poll


def _printer(**kwargs):
    kwargs.setdefault("name", "MS Office Printer")
    kwargs.setdefault("ip_address", "10.20.1.29")
    printer = Printer(**kwargs)
    printer.id = PRINTER
    printer.status_probe_failures = kwargs.get("status_probe_failures", 0)
    return printer


def _answers(monkeypatch):
    async def _probe(*_a, **_k):
        return PrinterStateResult(printer_state=3, state_reasons=["none"], state_message=None)

    monkeypatch.setattr(status, "probe_printer_state", _probe)


def _times_out(monkeypatch):
    async def _probe(*_a, **_k):
        raise PrinterProbeError("Timed out after 5s")

    monkeypatch.setattr(status, "probe_printer_state", _probe)


class _Cups:
    """Stands in for cupsd and the scripts that act on it."""

    def __init__(self, monkeypatch, printing=(POISON,), saturated=False, pause_fails=False):
        self.printing = printing
        self.paused: list[str] = []
        self.held: list[int] = []

        def _pause(printer_id):
            if pause_fails:
                raise QueuePauseError("cupsdisable failed")
            self.paused.append(printer_id)

        monkeypatch.setattr(status.cups_health, "is_saturated", lambda: saturated)
        monkeypatch.setattr(
            queue_recovery,
            "local_queue_state",
            lambda _pid: LocalQueueState(stopped=False, printing=tuple(self.printing)),
        )
        monkeypatch.setattr(queue_recovery, "pause_queue", _pause)
        monkeypatch.setattr(job_control, "hold_cups_job", self.held.append)
        monkeypatch.setattr(
            job_control,
            "held_cups_jobs",
            lambda _pid: [
                HeldCupsJob(
                    cups_job_id=job_id,
                    queue="release",
                    document_name="8th Grade Missing Work",
                    owner="a teacher",
                    size_bytes=437248,
                    submitted_at=None,
                )
                for job_id in self.held
            ],
        )


@pytest.fixture
def _no_other_queue_work(monkeypatch):
    """Queue recovery and stall detection have their own tests."""

    async def _noop_recovery(_printer, **_k):
        return False

    async def _noop_stall(_printer):
        return None

    monkeypatch.setattr(status, "_apply_queue_recovery", _noop_recovery)
    monkeypatch.setattr(status, "_apply_queue_stall", _noop_stall)


async def _two_missed_polls(printer):
    await status.refresh_printer_status(printer)
    await status.refresh_printer_status(printer)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_a_printer_lost_mid_job_has_its_queues_paused_and_nothing_held(monkeypatch):
    cups = _Cups(monkeypatch)
    printer = _printer(status="online")
    _times_out(monkeypatch)

    await _two_missed_polls(printer)

    assert printer.status == "offline"
    assert cups.paused == [PRINTER]
    assert cups.held == []
    assert crash_guard.paused_by_printops(PRINTER)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_the_m601_sequence_holds_the_job_at_the_second_loss(monkeypatch):
    """Crash, power-cycle, the same PDF again, crash again. Held — and the
    queue paused, so it restarts without it."""
    cups = _Cups(monkeypatch)
    printer = _printer(status="online")

    _times_out(monkeypatch)
    await _two_missed_polls(printer)
    _answers(monkeypatch)
    await status.refresh_printer_status(printer)
    assert printer.status == "online"
    # Queue recovery starts the paused queue once the printer answers (its own
    # tests are in test_queue_recovery.py; it is stubbed out here).
    crash_guard.note_resumed(PRINTER)
    _times_out(monkeypatch)
    await _two_missed_polls(printer)

    assert cups.held == [POISON]
    assert cups.paused == [PRINTER, PRINTER]
    [suspect] = crash_guard.held(PRINTER)
    assert suspect.document_name == "8th Grade Missing Work"
    assert suspect.owner == "a teacher"
    assert crash_guard.HELD_JOB_REASON in printer.status_reasons


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_a_held_job_is_what_the_printer_says_once_it_answers(monkeypatch):
    _Cups(monkeypatch)
    crash_guard.record_held(PRINTER, _suspect())
    printer = _printer(status="offline", status_probe_failures=2)
    _answers(monkeypatch)

    await status.refresh_printer_status(printer)
    await status.refresh_printer_status(printer)

    assert printer.status == "online"
    assert '"8th Grade Missing Work"' in printer.status_message
    # Once, however many polls it survives.
    assert printer.status_reasons.count(crash_guard.HELD_JOB_REASON) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_losing_a_printer_with_nothing_in_flight_touches_no_queue(monkeypatch):
    cups = _Cups(monkeypatch, printing=())
    printer = _printer(status="online")
    _times_out(monkeypatch)

    await _two_missed_polls(printer)

    assert cups.paused == []
    assert cups.held == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_one_missed_probe_is_not_a_loss(monkeypatch):
    cups = _Cups(monkeypatch)
    printer = _printer(status="online")
    _times_out(monkeypatch)

    await status.refresh_printer_status(printer)

    assert printer.status == "online"
    assert cups.paused == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_a_printer_already_offline_when_polling_starts_is_paused_once(monkeypatch):
    """PrintOps deployed or restarted in the middle of a crash loop: the row
    already reads offline, so there is no transition to see. The queue is
    paused on the first poll, and an outage that lasts a weekend adds nothing
    after that."""
    cups = _Cups(monkeypatch)
    printer = _printer(status="offline", status_probe_failures=2)
    _times_out(monkeypatch)

    for _ in range(5):
        await status.refresh_printer_status(printer)

    assert cups.paused == [PRINTER]
    assert crash_guard.strikes(PRINTER, POISON) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_nothing_is_done_while_cupsd_is_short_on_slots(monkeypatch):
    cups = _Cups(monkeypatch, saturated=True)
    printer = _printer(status="online")
    _times_out(monkeypatch)

    await _two_missed_polls(printer)

    assert cups.paused == []
    assert crash_guard.strikes(PRINTER, POISON) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_other_queue_work")
async def test_a_pause_that_fails_still_counts_the_loss(monkeypatch):
    """The hold is the part that ends the loop, so a refused pause must not
    also cost the strike."""
    cups = _Cups(monkeypatch, pause_fails=True)
    printer = _printer(status="online")

    for _ in range(2):
        _times_out(monkeypatch)
        await _two_missed_polls(printer)
        _answers(monkeypatch)
        await status.refresh_printer_status(printer)

    assert cups.held == [POISON]
    assert not crash_guard.paused_by_printops(PRINTER)


@pytest.mark.asyncio
async def test_a_queue_printops_paused_says_so_when_it_is_started_again(monkeypatch):
    """cupsd replaces the pause reason with the killed filter's last words
    ("gstoraster filter failed"), which describe nothing real. The message
    comes from what PrintOps did instead."""
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: False)
    monkeypatch.setattr(
        queue_recovery,
        "local_queue_state",
        lambda _pid: LocalQueueState(stopped=True, message="gstoraster filter failed."),
    )
    resumed = []
    monkeypatch.setattr(queue_recovery, "resume_queue", lambda pid, **_k: resumed.append(pid))
    crash_guard.note_paused(PRINTER)
    printer = _printer(status="online")

    assert await status._apply_queue_recovery(printer) is True

    assert resumed == [PRINTER]
    assert "PrintOps paused its queue" in printer.status_message
    assert "gstoraster" not in printer.status_message
    assert not crash_guard.paused_by_printops(PRINTER)
