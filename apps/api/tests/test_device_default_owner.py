"""Attributing a whole copier's meter to one named person.

The device in these tests is the one nobody logs in to: no Account Track,
no PIN, one regular user. Every test is really about one question — which
pages belong to the owner, and which belong to nobody because they were
made before anyone said so.
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.copiers.device_owner import attribute_device_copies
from app.models.base import Base
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.models.snmp import PrinterCounterReading

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite doesn't round-trip tzinfo through DateTime(timezone=True) the
    way Postgres does — same reason app/copiers/account_counters.py has its
    own _ensure_utc. Only the assertions need it; the module under test
    never compares a stored timestamp with a live one."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


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


@pytest_asyncio.fixture
async def printer(db):
    printer = Printer(
        name="IT Department Color Copier",
        ip_address="192.0.2.25",
        snmp_enabled=True,
        page_count_confidence="verified",
    )
    db.add(printer)
    await db.commit()
    await db.refresh(printer)
    return printer


@pytest_asyncio.fixture
async def device(db, printer):
    device = MfpDevice(
        name="IT Color Copier",
        vendor="canon",
        connector_type="canon_department_id",
        printer_id=printer.id,
        default_owner_email="manderson@brookfieldr3.org",
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def _reading(db, printer, copies, at):
    db.add(
        PrinterCounterReading(
            printer_id=printer.id,
            recorded_at=at,
            page_count_copy=copies,
            page_count_total=copies * 10,
            page_count_confidence="verified",
        )
    )
    await db.commit()


async def _usage(db):
    return (await db.execute(select(CopierUsageRecord))).scalars().all()


async def test_first_run_only_baselines_and_attributes_nothing(db, device, printer):
    """The meter reads 356 the day an owner is named. Those 356 copies were
    made before anyone said whose they were, and handing someone a
    device's whole lifetime on day one is the outcome that would make
    every number after it untrustworthy."""
    await _reading(db, printer, 356, NOW)

    result = await attribute_device_copies(db, device)
    await db.commit()

    assert result.baselined is True
    assert result.attributed_pages == 0
    assert await _usage(db) == []
    assert _utc(device.default_owner_attributed_through) == NOW


async def test_copies_since_the_baseline_belong_to_the_owner(db, device, printer):
    await _reading(db, printer, 356, NOW)
    await attribute_device_copies(db, device)
    await db.commit()

    await _reading(db, printer, 401, NOW + timedelta(hours=1))
    result = await attribute_device_copies(db, device)
    await db.commit()

    assert result.attributed_pages == 45
    rows = await _usage(db)
    assert len(rows) == 1
    assert rows[0].staff_email == "manderson@brookfieldr3.org"
    assert rows[0].page_count == 45
    assert rows[0].activity_type == "copy"
    # The window is the two readings it was measured between, not "now".
    assert _utc(rows[0].period_start) == NOW
    assert _utc(rows[0].period_end) == NOW + timedelta(hours=1)
    # No colour split exists on an SNMP copy meter, and inventing one
    # would misprice the row — see the module docstring.
    assert rows[0].color_page_count is None
    assert rows[0].monochrome_page_count is None
    assert rows[0].raw_payload["meter_from"] == 356
    assert rows[0].raw_payload["meter_to"] == 401


async def test_an_unmoved_meter_produces_nothing(db, device, printer):
    await _reading(db, printer, 356, NOW)
    await attribute_device_copies(db, device)
    await db.commit()

    await _reading(db, printer, 356, NOW + timedelta(hours=1))
    result = await attribute_device_copies(db, device)
    await db.commit()

    assert result.attributed_pages == 0
    assert await _usage(db) == []


async def test_running_twice_over_the_same_readings_does_not_double_count(db, device, printer):
    """The watermark is the whole defence against a lifetime meter being
    attributed again on every run — pressing the button twice must not
    charge the owner twice."""
    await _reading(db, printer, 356, NOW)
    await attribute_device_copies(db, device)
    await db.commit()
    await _reading(db, printer, 401, NOW + timedelta(hours=1))
    await attribute_device_copies(db, device)
    await db.commit()

    again = await attribute_device_copies(db, device)
    await db.commit()

    assert again.attributed_pages == 0
    rows = await _usage(db)
    assert len(rows) == 1
    assert sum(r.page_count for r in rows) == 45


async def test_a_replaced_meter_re_baselines_instead_of_attributing(db, device, printer):
    """A monotonic meter that reads lower was reset or the unit was
    swapped. Neither a negative nor the new absolute value is a count of
    what anyone copied, so counting restarts."""
    await _reading(db, printer, 9000, NOW)
    await attribute_device_copies(db, device)
    await db.commit()

    await _reading(db, printer, 12, NOW + timedelta(hours=1))
    result = await attribute_device_copies(db, device)
    await db.commit()

    assert result.meter_reset is True
    assert result.baselined is True
    assert await _usage(db) == []
    assert _utc(device.default_owner_attributed_through) == NOW + timedelta(hours=1)


async def test_a_device_with_no_owner_is_skipped(db, device, printer):
    device.default_owner_email = None
    await _reading(db, printer, 356, NOW)

    result = await attribute_device_copies(db, device)

    assert result.skipped_reason is not None
    assert result.attributed_pages == 0


async def test_a_meter_that_cannot_separate_copies_is_refused_with_a_reason(db, device, printer):
    """An "unsupported" printer reports one combined total. Inferring
    copies from it means subtracting PrintOps' own print jobs — an
    estimate the Untracked Copy Activity report already makes and labels
    as one. Handing that to a person under a measured number would
    misrepresent it."""
    printer.page_count_confidence = "unsupported"
    await db.commit()
    await _reading(db, printer, 356, NOW)

    result = await attribute_device_copies(db, device)

    assert result.attributed_pages == 0
    assert "combined page total" in result.skipped_reason


async def test_an_unlinked_copier_says_why_rather_than_failing_silently(db, device):
    device.printer_id = None
    await db.commit()

    result = await attribute_device_copies(db, device)

    assert "isn't linked to a printer" in result.skipped_reason


async def test_no_readings_yet_reads_as_not_yet_rather_than_no_copies(db, device, printer):
    result = await attribute_device_copies(db, device)

    assert result.attributed_pages == 0
    assert "no copy meter readings recorded yet" in result.skipped_reason


async def test_purged_history_baselines_off_the_oldest_surviving_reading(db, device, printer):
    """Counter history is purged on a retention schedule. The pages made
    before the oldest surviving reading are genuinely unrecoverable — the
    run must not invent them, and must not stall either."""
    device.default_owner_attributed_through = NOW - timedelta(days=365)
    await db.commit()
    await _reading(db, printer, 500, NOW)
    await _reading(db, printer, 530, NOW + timedelta(hours=1))

    result = await attribute_device_copies(db, device)
    await db.commit()

    assert result.attributed_pages == 30
