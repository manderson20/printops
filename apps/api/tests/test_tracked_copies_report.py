"""Reporting the copier activity PrintOps can put a name to."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
from app.reports.aggregation import ReportFilters
from app.reports.tracked_copies import get_tracked_copy_summary


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


async def _device(db, name="Veronica", polled=True, building="Elementary School"):
    device = MfpDevice(
        name=name,
        connector_type="konica_bizhub",
        building=building,
        last_counter_poll_at=datetime.now(UTC) if polled else None,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


async def _usage(db, device, email, pages, activity="copy", building="Elementary School"):
    db.add(
        CopierUsageRecord(
            mfp_device_id=device.id,
            vendor="konica_minolta",
            location_building=building,
            staff_email=email,
            external_identity_used="1",
            activity_type=activity,
            page_count=pages,
            source_connector="konica_bizhub",
            raw_payload={},
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_attributed_pages_are_totalled_per_device(db):
    device = await _device(db)
    await _usage(db, device, "bailey@d.org", 2)
    await _usage(db, device, "kensie@d.org", 8)

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert summary.copy_pages == 10
    assert summary.people == 2
    assert [(d.device_name, d.copy_pages, d.people) for d in summary.devices] == [
        ("Veronica", 10, 2)
    ]


@pytest.mark.asyncio
async def test_scans_and_faxes_are_kept_out_of_the_copy_total(db):
    """They are real usage but not pages produced at the glass; adding them
    to a copy count would overstate what the copier printed."""
    device = await _device(db)
    await _usage(db, device, "amy@d.org", 3, activity="copy")
    await _usage(db, device, "amy@d.org", 4, activity="scan")
    await _usage(db, device, "amy@d.org", 1, activity="fax")

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert (summary.copy_pages, summary.scan_pages, summary.fax_pages) == (3, 4, 1)


@pytest.mark.asyncio
async def test_unattributed_pages_are_reported_not_hidden_and_not_counted_as_tracked(db):
    """A copy against an account PrintOps didn't provision is real activity
    on a tracked copier. Folding it into the tracked total would overstate
    coverage; dropping it would lose the pages."""
    device = await _device(db)
    await _usage(db, device, "amy@d.org", 5)
    await _usage(db, device, None, 7)

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert summary.copy_pages == 12
    assert summary.unattributed_pages == 7
    # Nobody is a person, so the headcount stays at the one real person.
    assert summary.people == 1
    assert summary.devices[0].unattributed_pages == 7


@pytest.mark.asyncio
async def test_a_person_using_two_copiers_is_counted_once_overall(db):
    """Per-device headcounts sum to more than the district headcount, and
    the top-line number has to be the district one."""
    es = await _device(db, "Veronica")
    ms = await _device(db, "Cletus", building="Middle School")
    await _usage(db, es, "amy@d.org", 2)
    await _usage(db, ms, "amy@d.org", 3, building="Middle School")

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert summary.people == 1
    assert sum(d.people for d in summary.devices) == 2
    assert summary.copy_pages == 5


@pytest.mark.asyncio
async def test_devices_are_ordered_by_how_much_is_copied_on_them(db):
    quiet = await _device(db, "Quiet")
    busy = await _device(db, "Busy")
    await _usage(db, quiet, "amy@d.org", 1)
    await _usage(db, busy, "amy@d.org", 99)

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert [d.device_name for d in summary.devices] == ["Busy", "Quiet"]


@pytest.mark.asyncio
async def test_only_copiers_that_have_actually_been_read_count_as_reporting(db):
    """A copier with per-user accounting switched off must not be counted
    as covered — that is the number that says how much of the fleet this
    report can speak for."""
    await _device(db, "Polled", polled=True)
    await _device(db, "AccountingOff", polled=False)

    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert summary.devices_reporting == 1


@pytest.mark.asyncio
async def test_the_window_filter_excludes_older_activity(db):
    device = await _device(db)
    await _usage(db, device, "amy@d.org", 5)

    later = datetime.now(UTC) + timedelta(days=1)
    summary = await get_tracked_copy_summary(db, ReportFilters(start=later))

    assert summary.copy_pages == 0
    assert summary.devices == []


@pytest.mark.asyncio
async def test_nothing_recorded_yet_is_zeroes_not_an_error(db):
    summary = await get_tracked_copy_summary(db, ReportFilters())

    assert (summary.copy_pages, summary.people, summary.devices) == (0, 0, [])
