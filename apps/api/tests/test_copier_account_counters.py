"""Turning a copier's lifetime counters into usage.

The device reports running totals that only ever climb, so every test here
is really about one question: what did this account do *since last time*.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.copiers.account_counters import diff_counters, poll_account_counters
from app.copiers.connector import DeviceAccountCounters
from app.copiers.konica_admin import KonicaAdminSession, TrackCounters
from app.models.base import Base
from app.models.copier_account_counter import CopierAccountCounterReading
from app.models.copier_provisioning import CopierProvisionedAccount
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _counters(copy_bw=0, print_bw=0, scanned=0, faxed=0, copy_colour=0):
    return {
        "total": {"Bw": copy_bw + print_bw, "Paper": copy_bw + print_bw},
        "copy": {"Bw": copy_bw, "FullColor": copy_colour},
        "print": {"Bw": print_bw},
        "scan_fax": {"DocumentReadTotal": scanned, "FaxSend": faxed},
    }


class _FakeConnector:
    """Returns whatever the device is pretending to report this poll."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.reads = 0

    async def read_account_counters(self, device):
        snapshot = self.snapshots[min(self.reads, len(self.snapshots) - 1)]
        self.reads += 1
        return [DeviceAccountCounters(account_id=k, lists=v) for k, v in snapshot.items()]


@pytest_asyncio.fixture
async def device(db):
    device = MfpDevice(name="Veronica", connector_type="konica_bizhub", ip_address="192.0.2.10")
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def _own(db, device, account_id, email, provisioned_at=None, removed_at=None):
    db.add(
        CopierProvisionedAccount(
            mfp_device_id=device.id,
            staff_email=email,
            identity_value="10001",
            identity_type="staff_id",
            device_account_id=account_id,
            provisioned_at=provisioned_at or datetime(2026, 1, 1, tzinfo=UTC),
            removed_at=removed_at,
        )
    )
    await db.commit()


def _install(monkeypatch, connector):
    monkeypatch.setattr(
        "app.copiers.account_counters.get_connector", lambda connector_type: connector
    )


async def _usage(db):
    return (
        (await db.execute(select(CopierUsageRecord).order_by(CopierUsageRecord.activity_type)))
        .scalars()
        .all()
    )


# ---- the arithmetic ----


def test_delta_is_the_difference_not_the_reading():
    delta = diff_counters(_counters(copy_bw=100), _counters(copy_bw=140))
    assert delta.lists["copy"]["Bw"] == 40
    assert not delta.follows_reset


def test_unchanged_counters_produce_no_delta_at_all():
    delta = diff_counters(_counters(copy_bw=100), _counters(copy_bw=100))
    assert delta.lists == {}


def test_a_counter_that_went_down_means_it_was_cleared():
    """The only way a monotonic counter falls is the Counter Clear button.
    The current reading is then the usage since that clear, and the clear
    happened inside this window — so the reading is the delta rather than
    being thrown away."""
    delta = diff_counters(_counters(copy_bw=500), _counters(copy_bw=12))
    assert delta.follows_reset
    assert delta.lists["copy"]["Bw"] == 12


def test_a_counter_type_the_device_did_not_report_before_counts_from_zero():
    previous = {"copy": {"Bw": 10}}
    delta = diff_counters(previous, {"copy": {"Bw": 10, "FullColor": 3}})
    assert delta.lists == {"copy": {"FullColor": 3}}


def test_large_and_paper_counters_are_not_added_into_the_page_total():
    """BwLarge re-counts pages already in Bw, and Paper/Document count
    sheets and originals — including any of them would inflate the total."""
    delta = diff_counters(
        {"copy": {"Bw": 0, "BwLarge": 0, "BlackPrintPaper": 0}},
        {"copy": {"Bw": 10, "BwLarge": 4, "BlackPrintPaper": 10}},
    )
    assert delta.total_pages("copy") == 10


# ---- the poll ----


@pytest.mark.asyncio
async def test_first_poll_of_an_account_we_did_not_create_claims_no_usage(db, device, monkeypatch):
    """A copier that has been in service for years reports years of pages
    on the first read. With no provisioning record there is nothing to say
    where that counter started, so recording it would invent a day on which
    everyone copied their entire history."""
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=48211)}]))
    result = await poll_account_counters(db, device)

    assert result.baselines == 1
    assert result.usage_rows == 0
    assert await _usage(db) == []
    assert "first readings" in result.message


@pytest.mark.asyncio
async def test_first_poll_of_an_account_we_provisioned_counts_its_pages(db, device, monkeypatch):
    """PrintOps created this account, so its counter started at zero and
    the first reading is usage since provisioning — not history. Throwing
    it away loses real pages by real people between provisioning and the
    first poll."""
    await _own(db, device, "1", "bailey@d.org")
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=2)}]))
    result = await poll_account_counters(db, device)

    rows = await _usage(db)
    assert result.baseline_usage_rows == 1
    assert [(r.activity_type, r.page_count, r.staff_email) for r in rows] == [
        ("copy", 2, "bailey@d.org")
    ]
    # Flagged, because a sync that rewrote an adopted account rather than
    # creating it could carry pages from before PrintOps was involved.
    assert rows[0].raw_payload["from_provisioning_baseline"] is True
    assert "usage since provisioning" in result.message


@pytest.mark.asyncio
async def test_a_provisioned_account_that_has_done_nothing_yet_records_nothing(
    db, device, monkeypatch
):
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=0)}]))
    await _own(db, device, "1", "bailey@d.org")
    result = await poll_account_counters(db, device)

    assert result.baseline_usage_rows == 0
    assert await _usage(db) == []


@pytest.mark.asyncio
async def test_the_baseline_window_starts_when_the_account_was_provisioned(db, device, monkeypatch):
    """Not at the first poll — the pages were made across the whole span
    between the account existing and PrintOps first looking at it."""
    provisioned = datetime(2026, 8, 17, 8, 37, tzinfo=UTC)
    await _own(db, device, "1", "bailey@d.org", provisioned_at=provisioned)
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=2)}]))
    at = datetime(2026, 8, 17, 16, 36, tzinfo=UTC)
    await poll_account_counters(db, device, now=at)

    row = (await _usage(db))[0]
    assert row.period_start.replace(tzinfo=UTC) == provisioned
    assert row.period_end.replace(tzinfo=UTC) == at


@pytest.mark.asyncio
async def test_a_baseline_is_counted_once_and_not_again(db, device, monkeypatch):
    """The reading is stored, so the next poll has something to subtract
    from and must not re-report the same pages."""
    await _own(db, device, "1", "bailey@d.org")
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=2)}]))
    await poll_account_counters(db, device)
    await poll_account_counters(db, device)

    rows = await _usage(db)
    assert [r.page_count for r in rows] == [2]


@pytest.mark.asyncio
async def test_second_poll_records_only_what_happened_in_between(db, device, monkeypatch):
    await _own(db, device, "1", "amy@d.org")
    _install(
        monkeypatch,
        _FakeConnector([{"1": _counters(copy_bw=0)}, {"1": _counters(copy_bw=50)}]),
    )
    await poll_account_counters(db, device)
    result = await poll_account_counters(db, device)

    rows = await _usage(db)
    assert result.usage_rows == 1
    assert [(r.activity_type, r.page_count, r.staff_email) for r in rows] == [
        ("copy", 50, "amy@d.org")
    ]


@pytest.mark.asyncio
async def test_usage_window_starts_at_the_last_poll_not_the_last_change(db, device, monkeypatch):
    """Three polls, with the copy happening between the second and third.
    The window must be [second poll, third poll] — dating it back to the
    first would spread 50 pages across days nobody touched the machine."""
    await _own(db, device, "1", "amy@d.org")
    _install(
        monkeypatch,
        _FakeConnector(
            [
                {"1": _counters(copy_bw=0)},
                {"1": _counters(copy_bw=0)},
                {"1": _counters(copy_bw=50)},
            ]
        ),
    )
    first = datetime(2026, 8, 1, tzinfo=UTC)
    await poll_account_counters(db, device, now=first)
    await poll_account_counters(db, device, now=first + timedelta(hours=1))
    await poll_account_counters(db, device, now=first + timedelta(hours=2))

    row = (await _usage(db))[0]
    # SQLite drops tzinfo on the way back out; Postgres doesn't.
    assert row.period_start.replace(tzinfo=UTC) == first + timedelta(hours=1)
    assert row.period_end.replace(tzinfo=UTC) == first + timedelta(hours=2)


@pytest.mark.asyncio
async def test_an_idle_account_keeps_one_reading_row(db, device, monkeypatch):
    """Polling hourly forever must not write a row per poll per account."""
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=10)}]))
    for _ in range(5):
        await poll_account_counters(db, device)

    readings = (await db.execute(select(CopierAccountCounterReading))).scalars().all()
    assert len(readings) == 1
    assert readings[0].superseded_at is None


@pytest.mark.asyncio
async def test_print_counters_are_not_recorded_as_copier_usage(db, device, monkeypatch):
    """Print pages arrive at the copier from the print server, which
    PrintOps already records as Job rows — counting them again here would
    double every printed page in combined reporting."""
    await _own(db, device, "1", "amy@d.org")
    _install(
        monkeypatch,
        _FakeConnector(
            [
                {"1": _counters(copy_bw=0, print_bw=0)},
                {"1": _counters(copy_bw=5, print_bw=90)},
            ]
        ),
    )
    await poll_account_counters(db, device)
    await poll_account_counters(db, device)

    rows = await _usage(db)
    assert [(r.activity_type, r.page_count) for r in rows] == [("copy", 5)]
    # Not lost, though — the print delta is on the row for audit.
    assert rows[0].raw_payload["counter_delta"]["print"]["Bw"] == 90


@pytest.mark.asyncio
async def test_scans_and_faxes_are_their_own_activities(db, device, monkeypatch):
    await _own(db, device, "1", "amy@d.org")
    _install(
        monkeypatch,
        _FakeConnector(
            [
                {"1": _counters()},
                {"1": _counters(copy_bw=2, scanned=7, faxed=3)},
            ]
        ),
    )
    await poll_account_counters(db, device)
    await poll_account_counters(db, device)

    rows = await _usage(db)
    assert [(r.activity_type, r.page_count) for r in rows] == [
        ("copy", 2),
        ("fax", 3),
        ("scan", 7),
    ]


@pytest.mark.asyncio
async def test_usage_on_an_unknown_account_is_kept_unattributed(db, device, monkeypatch):
    """An account somebody created by hand at the panel. The pages are
    real, so they are recorded — with no staff_email, which is what puts
    them in front of an admin in the Unmapped Activity flow instead of
    quietly dropping them."""
    _install(
        monkeypatch,
        _FakeConnector([{"9": _counters(copy_bw=0)}, {"9": _counters(copy_bw=4)}]),
    )
    await poll_account_counters(db, device)
    result = await poll_account_counters(db, device)

    row = (await _usage(db))[0]
    assert result.unmapped == 1
    assert row.staff_email is None
    assert row.external_identity_used == "9"


@pytest.mark.asyncio
async def test_a_reused_account_number_does_not_inherit_the_last_persons_pages(
    db, device, monkeypatch
):
    """Account 1 belonged to amy until July; bob has it now. Pages read
    today are bob's."""
    await _own(
        db,
        device,
        "1",
        "amy@d.org",
        provisioned_at=datetime(2026, 1, 1, tzinfo=UTC),
        removed_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await _own(db, device, "1", "bob@d.org", provisioned_at=datetime(2026, 7, 2, tzinfo=UTC))
    _install(
        monkeypatch,
        _FakeConnector([{"1": _counters(copy_bw=0)}, {"1": _counters(copy_bw=9)}]),
    )
    await poll_account_counters(db, device)
    await poll_account_counters(db, device)

    assert (await _usage(db))[0].staff_email == "bob@d.org"


@pytest.mark.asyncio
async def test_a_cleared_counter_is_recorded_and_flagged(db, device, monkeypatch):
    await _own(db, device, "1", "amy@d.org")
    _install(
        monkeypatch,
        _FakeConnector([{"1": _counters(copy_bw=500)}, {"1": _counters(copy_bw=6)}]),
    )
    await poll_account_counters(db, device)
    result = await poll_account_counters(db, device)

    # The baseline row (500 pages since provisioning) is its own record;
    # the one under test is the row the reset produced.
    row = next(r for r in await _usage(db) if r.raw_payload.get("follows_counter_reset"))
    assert result.resets == 1
    assert row.page_count == 6
    assert row.raw_payload["follows_counter_reset"] is True


@pytest.mark.asyncio
async def test_the_poll_result_is_recorded_on_the_device(db, device, monkeypatch):
    """So "when did this last work" is answerable from the device page."""
    _install(monkeypatch, _FakeConnector([{"1": _counters(copy_bw=1)}]))
    at = datetime(2026, 8, 17, 12, tzinfo=UTC)
    await poll_account_counters(db, device, now=at)

    assert device.last_counter_poll_at == at
    assert device.last_counter_poll_ok is True
    assert "1 accounts read" in device.last_counter_poll_message


# ---- the device contract ----


@pytest.mark.asyncio
async def test_counter_read_pages_in_fifties_and_stops_past_the_end():
    """The device takes ID windows, not offsets, and answers a window past
    the last account with an empty list rather than an error — so paging
    has to stop on emptiness, not on a short page."""
    calls = []

    class _Session:
        async def webapi(self, endpoint, payload=None):
            window = payload["TrackCounterListCondition"]["ObtainCondition"]["IndexRange"]
            calls.append((window["Start"], window["End"]))
            ids = [i for i in range(window["Start"], window["End"] + 1) if i <= 60]
            return {
                "TrackCounterList": {
                    "ArraySize": str(len(ids)),
                    "TrackCounter": [
                        {
                            "TrackID": str(i),
                            "CopyCounterList": {"Counter": [{"Type": "Bw", "Count": "3"}]},
                        }
                        for i in ids
                    ],
                }
            }

    counters = await KonicaAdminSession.list_account_counters(_Session())
    assert calls[:2] == [(1, 50), (51, 100)]
    assert len(counters) == 60
    assert counters[0].lists["copy"] == {"Bw": 3}
    # Stopped after the empty windows rather than walking to 1000.
    assert len(calls) == 4


def test_a_single_counter_is_parsed_like_a_list_of_one():
    """The device collapses one-element arrays into an object, the same
    trap as the account list."""
    parsed = TrackCounters.from_device(
        {"TrackID": "7", "CopyCounterList": {"Counter": {"Type": "Bw", "Count": "5"}}}
    )
    assert parsed.lists["copy"] == {"Bw": 5}
