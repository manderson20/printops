"""app/printers/network_health.py — noticing a printer whose network path is
dropping traffic while the printer itself answers fine.

The case: the LCACTC RM 502 was moved onto a switch port that had not been used
in a long time, and from 2026-08-23 lost ~18% of the packets sent to it — 7
second blackouts every 37 seconds, under continuous traffic, with the device's
own interface counters reporting zero errors throughout. Every signal PrintOps
collects comes from the device, and the device was fine.

The offline debounce (0.59.6) stopped PrintOps flapping the printer offline
over it, which was right and which also meant nobody would ever be told. This
is what tells them.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models.printer import Printer
from app.printers import network_health, status

PRINTER = "2ea028f4-abc2-423b-9204-aaf47c6a9be2"
T0 = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _observe(outcomes, start=T0, every=timedelta(minutes=1), log=None):
    """Runs a sequence of probe outcomes through the log the way the status
    poll does — read the row, observe, write it back."""
    verdict = None
    for i, answered in enumerate(outcomes):
        log, verdict = network_health.observe(log, answered, now=start + every * i)
    return log, verdict


def test_a_healthy_printer_never_raises_anything():
    assert _observe([True] * 60)[1] is None


def test_one_missed_probe_is_not_an_alarm():
    """Ordinary. The whole reason the offline debounce exists."""
    assert _observe([True, False, True, True])[1] is None


def test_two_missed_probes_are_still_not():
    assert _observe([True, False, True, False, True])[1] is None


def test_three_misses_with_answers_in_between_are():
    _, flap = _observe([True, False, True, False, True, False, True])
    assert flap is not None
    assert flap.misses == 3
    assert flap.probes == 7


def test_a_printer_that_is_simply_gone_is_not_reported_as_lossy():
    """It misses every probe and answers none. "Offline" says that better than
    any loss rate, and this must not talk over it."""
    assert _observe([False] * 20)[1] is None


def test_misses_outside_the_window_stop_counting():
    """A path that was lossy this morning and is fine now is not lossy."""
    log, old = _observe([False, False, False, True], every=timedelta(minutes=1))
    assert old is not None
    # Two hours later, all of those have aged out — and are dropped from the
    # stored log rather than accumulating forever.
    log, later = network_health.observe(log, True, now=T0 + timedelta(hours=2))
    assert later is None
    assert len(log) == 1


def test_the_loss_rate_is_reported_from_what_was_seen():
    _, flap = _observe([True, False] * 10)
    assert flap is not None
    assert flap.probes == 20
    assert flap.misses == 10
    assert flap.loss_percent == 50


def test_the_message_says_what_was_seen_and_where_to_look():
    _, flap = _observe([True, False, True, False, True, False, True])
    assert flap is not None
    reason = network_health.flap_reason(flap)
    assert "4 of 7" in reason
    assert "43%" in reason
    assert "switch port" in reason
    # Says what it is *not*, so nobody goes hunting in the printer.
    assert "rather than a fault on the printer" in reason


def test_a_log_survives_a_restart_because_it_is_not_in_memory():
    """The reason this moved onto the printer row (migration 0063): a probe
    that went unanswered stays unanswered across an API restart, and a deploy
    must not hand a lossy printer a fresh hour of looking healthy."""
    log, _ = _observe([True, False, True, False, True])
    # Round-trip it the way the JSON column does, then carry on.
    restarted = json.loads(json.dumps(log))
    _, flap = network_health.observe(restarted, False, now=T0 + timedelta(minutes=5))
    assert flap is not None
    assert flap.misses == 3


def test_a_malformed_entry_is_dropped_rather_than_raising():
    """A rolling diagnostic must not be able to take the status poll down."""
    log, flap = network_health.observe(
        [["not-a-timestamp", True], "nonsense", [T0.isoformat(), False]], False, now=T0
    )
    assert [entry[1] for entry in log] == [False, False]
    assert flap is None


# ---- how it reaches the printer row ----


def _printer(**kwargs):
    kwargs.setdefault("name", "LCACTC - RM 502 Color Printer")
    kwargs.setdefault("ip_address", "10.50.1.30")
    printer = Printer(**kwargs)
    printer.id = PRINTER
    return printer


def _flap():
    return network_health.NetworkFlap(misses=7, probes=60, window=timedelta(hours=1))


def test_a_flapping_printer_is_flagged_but_stays_online():
    """It is printing. A red "error" badge on a working printer is how people
    are taught to ignore the field."""
    printer = _printer(status="online")

    status._apply_network_flap(printer, _flap())

    assert printer.status == "online"
    assert network_health.NETWORK_UNSTABLE_REASON in printer.status_reasons
    assert "12%" in printer.status_message


def test_nothing_is_said_when_there_is_no_flap():
    printer = _printer(status="online")

    status._apply_network_flap(printer, None)

    assert printer.status_reasons is None
    assert printer.status_message is None


@pytest.mark.parametrize("existing", ["error", "offline"])
def test_a_more_specific_diagnosis_is_not_talked_over(existing):
    """A stopped or stalled queue is something an admin can act on directly;
    a lossy path is background next to it."""
    printer = _printer(status=existing, status_message="Queue paused")

    status._apply_network_flap(printer, _flap())

    assert printer.status_message == "Queue paused"
    assert printer.status_reasons is None
