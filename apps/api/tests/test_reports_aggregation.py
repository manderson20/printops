from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.reports.aggregation import (
    ReportFilters,
    get_peak_times,
    get_printer_leaderboard,
    get_summary,
    get_timeline,
    get_user_leaderboard,
    physical_sheets_used,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


def _dt(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=UTC)


@pytest_asyncio.fixture
async def two_printers(session):
    a = Printer(name="Library Copier", ip_address="10.0.0.1", building="Main", department="Library")
    b = Printer(name="Office Printer", ip_address="10.0.0.2", building="Annex", department="Admin")
    session.add_all([a, b])
    await session.commit()
    await session.refresh(a)
    await session.refresh(b)
    return a, b


@pytest_asyncio.fixture
async def sample_jobs(session, two_printers):
    library, office = two_printers
    jobs = [
        Job(
            printer_id=library.id,
            submitted_by="alice@example.com",
            status="forwarded",
            page_count=10,
            color_mode="color",
            duplex=True,
            created_at=_dt(2026, 3, 3, 8),
        ),
        Job(
            printer_id=library.id,
            submitted_by="alice@example.com",
            status="forwarded",
            page_count=20,
            color_mode="monochrome",
            duplex=False,
            created_at=_dt(2026, 3, 3, 9),
        ),
        Job(
            printer_id=office.id,
            submitted_by="bob@example.com",
            status="failed",
            page_count=5,
            color_mode=None,
            duplex=None,
            created_at=_dt(2026, 3, 5, 14),
        ),
        Job(
            printer_id=office.id,
            submitted_by="bob@example.com",
            status="cancelled",
            page_count=7,
            color_mode="color",
            duplex=False,
            created_at=_dt(2026, 3, 5, 14),
        ),
    ]
    session.add_all(jobs)
    await session.commit()
    return jobs


async def test_summary_totals(session, sample_jobs):
    summary = await get_summary(session, ReportFilters())
    assert summary.total_jobs == 4
    assert summary.total_pages == 42
    assert summary.forwarded_jobs == 2
    assert summary.failed_jobs == 1
    assert summary.cancelled_jobs == 1
    assert summary.color_pages == 17  # 10 + 7
    assert summary.mono_pages == 20
    assert summary.unknown_color_mode_pages == 5
    assert summary.duplex_pages == 10
    assert summary.simplex_pages == 27  # 20 + 7
    assert summary.unknown_duplex_pages == 5


async def test_summary_filters_by_building(session, sample_jobs):
    summary = await get_summary(session, ReportFilters(building="Main"))
    assert summary.total_jobs == 2
    assert summary.total_pages == 30


async def test_summary_filters_by_date_range(session, sample_jobs):
    summary = await get_summary(
        session, ReportFilters(start=_dt(2026, 3, 4), end=_dt(2026, 3, 6))
    )
    assert summary.total_jobs == 2
    assert summary.total_pages == 12


async def test_summary_filters_by_submitted_by(session, sample_jobs):
    summary = await get_summary(session, ReportFilters(submitted_by="alice@example.com"))
    assert summary.total_jobs == 2


async def test_timeline_buckets_by_day(session, sample_jobs):
    buckets = await get_timeline(session, ReportFilters(), granularity="day")
    assert [b.bucket_start.isoformat() for b in buckets] == ["2026-03-03", "2026-03-05"]
    assert buckets[0].total_pages == 30
    assert buckets[1].total_pages == 12


async def test_peak_times_by_hour(session, sample_jobs):
    peak = await get_peak_times(session, ReportFilters())
    assert peak.by_hour[8] == 10
    assert peak.by_hour[9] == 20
    assert peak.by_hour[14] == 12


async def test_printer_leaderboard_orders_by_job_count(session, sample_jobs, two_printers):
    library, office = two_printers
    board = await get_printer_leaderboard(session, ReportFilters())
    by_key = {entry.key: entry for entry in board}
    assert by_key[str(library.id)].job_count == 2
    assert by_key[str(library.id)].total_pages == 30
    assert by_key[str(office.id)].job_count == 2
    assert by_key[str(office.id)].total_pages == 12


async def test_user_leaderboard_groups_by_submitted_by(session, sample_jobs):
    board = await get_user_leaderboard(session, ReportFilters())
    by_user = {entry.key: entry for entry in board}
    assert by_user["alice@example.com"].job_count == 2
    assert by_user["bob@example.com"].job_count == 2
    assert by_user["bob@example.com"].total_pages == 12


@pytest.mark.parametrize(
    "page_count,duplex,expected",
    [
        (10, True, 5),
        (11, True, 6),
        (10, False, 10),
        (10, None, 10),
    ],
)
def test_physical_sheets_used(page_count, duplex, expected):
    assert physical_sheets_used(page_count, duplex) == expected
