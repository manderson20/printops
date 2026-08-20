"""Keeping PrintOps' own queue maintenance from starving cupsd.

The failure these guard against: a flapping printer transitioned
offline->online every few minutes, each transition triggering a full queue
resync, each resync holding a cupsd client slot for `lpadmin -m
everywhere`'s 30-second timeout — until all 100 MaxClients slots were gone
and every print client on the server hung on "waiting for communication".
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.printer import Printer
from app.printers import cups_health, status
from app.printers.queue_sync import QueueSyncError


def _printer(**kwargs):
    return Printer(name="Nurse Copier", ip_address="192.0.2.5", **kwargs)


def _at(hours_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours_ago)


# ---- the cooldown ----


def test_a_printer_never_auto_synced_is_due():
    assert status.auto_resync_due(_printer()) is True


def test_a_printer_synced_moments_ago_is_not_due():
    """The flapping case: back online again five minutes later."""
    assert status.auto_resync_due(_printer(last_auto_queue_sync_at=_at(0.08))) is False


def test_a_printer_back_after_a_night_off_is_due_immediately():
    """The legitimate case the resync exists for — switched off overnight,
    turned on in the morning, PPD possibly built from the degraded
    fallback. It must not be made to wait."""
    assert status.auto_resync_due(_printer(last_auto_queue_sync_at=_at(14))) is True


def test_repeated_failures_back_off_further_each_time():
    """A resync that just failed is the least likely to succeed if retried
    immediately, and failing ones are the expensive ones."""
    one_hour_ago = _at(1)
    assert status.auto_resync_due(_printer(last_auto_queue_sync_at=one_hour_ago)) is True
    assert (
        status.auto_resync_due(
            _printer(last_auto_queue_sync_at=one_hour_ago, auto_queue_sync_failures=3)
        )
        is False
    )


def test_backoff_is_capped_so_a_printer_is_never_abandoned():
    """However badly it has been failing, a printer gets another chance
    within the cap — otherwise a device fixed after a bad week would never
    resync on its own again."""
    assert (
        status.auto_resync_due(
            _printer(last_auto_queue_sync_at=_at(7), auto_queue_sync_failures=99)
        )
        is True
    )


# ---- the cupsd saturation gate ----


def test_no_sync_is_started_while_cupsd_is_short_on_slots(monkeypatch):
    monkeypatch.setattr(cups_health, "open_connections", lambda: 70)
    monkeypatch.setattr(cups_health, "max_clients", lambda: 100)
    assert cups_health.is_saturated() is True


def test_a_quiet_scheduler_is_not_saturated(monkeypatch):
    monkeypatch.setattr(cups_health, "open_connections", lambda: 4)
    monkeypatch.setattr(cups_health, "max_clients", lambda: 100)
    assert cups_health.is_saturated() is False


def test_an_unreadable_proc_does_not_stop_queue_maintenance_forever(monkeypatch):
    """Failing to read /proc must mean "no reason to hold back", not
    "assume the worst" — the latter would silently disable all queue
    maintenance on any host where the check doesn't work."""
    monkeypatch.setattr(cups_health, "open_connections", lambda: None)
    assert cups_health.is_saturated() is False


def test_max_clients_falls_back_to_the_cups_default(monkeypatch, tmp_path):
    """cupsd.conf usually has no MaxClients line at all; the ceiling is
    still 100, and reading it as 0 would make every check look saturated."""
    conf = tmp_path / "cupsd.conf"
    conf.write_text("LogLevel warn\nListen localhost:631\n")
    monkeypatch.setattr(cups_health, "CUPSD_CONF", conf)
    assert cups_health.max_clients() == cups_health.DEFAULT_MAX_CLIENTS


def test_a_configured_max_clients_is_honoured(monkeypatch, tmp_path):
    conf = tmp_path / "cupsd.conf"
    conf.write_text("LogLevel warn\nMaxClients 500\n")
    monkeypatch.setattr(cups_health, "CUPSD_CONF", conf)
    assert cups_health.max_clients() == 500


# ---- the whole gate ----


@pytest.mark.asyncio
async def test_a_saturated_scheduler_is_left_alone(monkeypatch):
    calls = []
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: True)
    monkeypatch.setattr(status, "sync_queue", lambda pid: calls.append(pid))

    printer = _printer()
    assert await status.run_automatic_queue_sync(printer) is False
    assert calls == []
    # Not stamped: nothing was attempted, so the printer owes no cooldown
    # for the scheduler having been busy.
    assert printer.last_auto_queue_sync_at is None


@pytest.mark.asyncio
async def test_a_successful_sync_clears_the_failure_count(monkeypatch):
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: False)
    monkeypatch.setattr(status, "sync_queue", lambda pid: None)

    printer = _printer(auto_queue_sync_failures=4)
    assert await status.run_automatic_queue_sync(printer) is True
    assert printer.auto_queue_sync_failures == 0
    assert printer.queue_sync_error is None
    assert printer.last_auto_queue_sync_at is not None


@pytest.mark.asyncio
async def test_a_failed_sync_counts_toward_the_backoff_and_is_not_raised(monkeypatch):
    """One printer's failure must not take down the loop refreshing all the
    others."""

    def _boom(pid):
        raise QueueSyncError("Unable to connect to 192.0.2.5:631")

    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: False)
    monkeypatch.setattr(status, "sync_queue", _boom)

    printer = _printer(auto_queue_sync_failures=1)
    assert await status.run_automatic_queue_sync(printer) is True
    assert printer.auto_queue_sync_failures == 2
    assert "Unable to connect" in printer.queue_sync_error


@pytest.mark.asyncio
async def test_the_flapping_printer_gets_one_resync_not_a_hundred(monkeypatch):
    """The actual regression: 137 resyncs in one day from a single copier
    going offline and back every few minutes."""
    calls = []
    monkeypatch.setattr(status.cups_health, "is_saturated", lambda: False)
    monkeypatch.setattr(status, "sync_queue", lambda pid: calls.append(pid))

    printer = _printer()
    for _ in range(50):
        await status.run_automatic_queue_sync(printer)

    assert len(calls) == 1
