"""app/printers/status.py — how many missed probes it takes to call a printer
offline.

The fault: one failed IPP probe marked a printer offline, and a printer on a
lossy path misses one routinely. Measured on the LCACTC RM 502 (an HP M652)
on 2026-08-23: 17.5% of 120 packets lost, in blackouts of 7 seconds arriving
37 seconds apart (bursts began at t=23s, 60s and 97s), under continuous
once-a-second traffic. The device's own interface counters are clean, so the
traffic never reaches it — the cause is somewhere on the path and is still
open. Each false "offline" cost a capability rediscovery and a full queue
resync when it flipped back.
"""

import pytest

from app.models.printer import Printer
from app.printers import status
from app.printers.ipp_client import PrinterProbeError, PrinterStateResult

PRINTER = "2ea028f4-abc2-423b-9204-aaf47c6a9be2"


def _printer(**kwargs):
    kwargs.setdefault("name", "LCACTC - RM 502 Color Printer")
    kwargs.setdefault("ip_address", "10.50.1.30")
    printer = Printer(**kwargs)
    printer.id = PRINTER
    printer.status_probe_failures = kwargs.get("status_probe_failures", 0)
    return printer


def _answers(monkeypatch, printer_state=3, state_reasons=None):
    async def _probe(*_a, **_k):
        return PrinterStateResult(
            printer_state=printer_state,
            state_reasons=state_reasons or ["none"],
            state_message=None,
        )

    monkeypatch.setattr(status, "probe_printer_state", _probe)


def _times_out(monkeypatch):
    async def _probe(*_a, **_k):
        raise PrinterProbeError("Timed out after 5s")

    monkeypatch.setattr(status, "probe_printer_state", _probe)


@pytest.fixture(autouse=True)
def _no_queue_work(monkeypatch):
    """Queue recovery and stall detection have their own tests; this file is
    only about what the probe result does to `status`."""

    async def _noop_recovery(_printer, **_k):
        return False

    async def _noop_stall(_printer):
        return None

    monkeypatch.setattr(status, "_apply_queue_recovery", _noop_recovery)
    monkeypatch.setattr(status, "_apply_queue_stall", _noop_stall)


@pytest.mark.asyncio
async def test_one_missed_probe_does_not_make_a_printer_offline(monkeypatch):
    """A sleeping printer misses the first packet of a cold call. That is not
    the same thing as a printer being gone."""
    _times_out(monkeypatch)
    printer = _printer(status="online")

    await status.refresh_printer_status(printer)

    assert printer.status == "online"
    assert printer.status_probe_failures == 1
    # Still recorded as checked — the poll did run, it just didn't conclude.
    assert printer.status_checked_at is not None


@pytest.mark.asyncio
async def test_two_missed_probes_in_a_row_do(monkeypatch):
    _times_out(monkeypatch)
    printer = _printer(status="online")

    await status.refresh_printer_status(printer)
    await status.refresh_printer_status(printer)

    assert printer.status == "offline"
    assert printer.status_message == "Timed out after 5s"
    assert printer.status_probe_failures == 2


@pytest.mark.asyncio
async def test_an_answer_in_between_clears_the_count(monkeypatch):
    """The flapping case: miss, answer, miss. Two failures, never consecutive,
    so the printer was never offline."""
    printer = _printer(status="online")

    _times_out(monkeypatch)
    await status.refresh_printer_status(printer)
    _answers(monkeypatch)
    await status.refresh_printer_status(printer)
    _times_out(monkeypatch)
    await status.refresh_printer_status(printer)

    assert printer.status == "online"
    assert printer.status_probe_failures == 1


@pytest.mark.asyncio
async def test_a_printer_that_is_really_gone_is_reported_within_two_cycles(monkeypatch):
    _times_out(monkeypatch)
    printer = _printer(status="online")

    for _ in range(4):
        await status.refresh_printer_status(printer)

    assert printer.status == "offline"


@pytest.mark.asyncio
async def test_coming_back_clears_both_the_status_and_the_count(monkeypatch):
    _times_out(monkeypatch)
    printer = _printer(status="online")
    await status.refresh_printer_status(printer)
    await status.refresh_printer_status(printer)
    assert printer.status == "offline"

    _answers(monkeypatch)
    await status.refresh_printer_status(printer)

    assert printer.status == "online"
    assert printer.status_probe_failures == 0
    assert printer.status_message is None


@pytest.mark.asyncio
async def test_a_manual_check_is_held_to_the_same_standard(monkeypatch):
    """The button asks the same question over the same timeout. A faster
    answer that is wrong isn't worth more to the person clicking it."""
    _times_out(monkeypatch)
    printer = _printer(status="online")

    await status.refresh_printer_status(printer, manual=True)

    assert printer.status == "online"
    assert printer.status_probe_failures == 1


@pytest.mark.asyncio
async def test_an_error_state_from_the_device_still_lands_immediately(monkeypatch):
    """Debouncing is about probes that don't come back. A printer that answers
    and says it is jammed is answering, and gets reported at once."""
    _answers(monkeypatch, printer_state=3, state_reasons=["media-jam-error"])
    printer = _printer(status="online")

    await status.refresh_printer_status(printer)

    assert printer.status == "error"
    assert printer.status_reasons == ["media-jam-error"]
    assert printer.status_probe_failures == 0
