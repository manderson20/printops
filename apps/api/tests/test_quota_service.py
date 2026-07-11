from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.models.quota import PrinterUserQuota, QuotaSettings
from app.quotas.service import (
    get_effective_quota,
    get_pages_used,
    period_bounds,
    resolve_hold_reason,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_printer(session_factory, **kwargs) -> Printer:
    async with session_factory() as session:
        printer = Printer(name="Color Printer", ip_address="10.0.0.5", **kwargs)
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer


async def _make_job(session_factory, printer_id, submitted_by, page_count, created_at):
    async with session_factory() as session:
        job = Job(
            printer_id=printer_id,
            submitted_by=submitted_by,
            page_count=page_count,
            status="forwarded",
            created_at=created_at,
        )
        session.add(job)
        await session.commit()


def test_period_bounds_monthly_crosses_year():
    now = datetime(2026, 12, 15, 12, 0, tzinfo=UTC)
    start, end = period_bounds("monthly", now)
    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_period_bounds_quarterly():
    now = datetime(2026, 11, 3, tzinfo=UTC)
    start, end = period_bounds("quarterly", now)
    assert start == datetime(2026, 10, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_period_bounds_weekly_monday_start():
    # 2026-07-08 is a Wednesday.
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    start, end = period_bounds("weekly", now)
    assert start == datetime(2026, 7, 6, tzinfo=UTC)
    assert end == datetime(2026, 7, 13, tzinfo=UTC)


async def test_get_effective_quota_prefers_specific_over_default(session_factory):
    printer = await _make_printer(session_factory)
    async with session_factory() as session:
        session.add(
            PrinterUserQuota(
                printer_id=printer.id, user_email=None, period="monthly", page_limit=100
            )
        )
        session.add(
            PrinterUserQuota(
                printer_id=printer.id, user_email="matt@example.org", period="daily", page_limit=10
            )
        )
        await session.commit()

    async with session_factory() as session:
        quota = await get_effective_quota(session, printer.id, "matt@example.org")
        assert quota is not None
        assert quota.period == "daily" and quota.page_limit == 10

        fallback = await get_effective_quota(session, printer.id, "someone.else@example.org")
        assert fallback is not None
        assert fallback.period == "monthly" and fallback.page_limit == 100


async def test_get_effective_quota_none_when_unconfigured(session_factory):
    printer = await _make_printer(session_factory)
    async with session_factory() as session:
        quota = await get_effective_quota(session, printer.id, "nobody@example.org")
        assert quota is None


async def test_get_pages_used_scopes_by_printer_user_and_range(session_factory):
    printer = await _make_printer(session_factory)
    other_printer = await _make_printer(session_factory)
    in_range = datetime(2026, 7, 15, tzinfo=UTC)
    out_of_range = datetime(2026, 6, 15, tzinfo=UTC)

    await _make_job(session_factory, printer.id, "matt@example.org", 10, in_range)
    await _make_job(session_factory, printer.id, "matt@example.org", 5, in_range)
    await _make_job(session_factory, printer.id, "matt@example.org", 999, out_of_range)
    await _make_job(session_factory, printer.id, "someone.else@example.org", 999, in_range)
    await _make_job(session_factory, other_printer.id, "matt@example.org", 999, in_range)
    # In-flight job (no page_count yet) should contribute 0, not error.
    await _make_job(session_factory, printer.id, "matt@example.org", None, in_range)

    start, end = period_bounds("monthly", in_range)
    async with session_factory() as session:
        used = await get_pages_used(session, printer.id, "matt@example.org", start, end)
        assert used == 15


async def test_resolve_hold_reason_pin_release_wins(session_factory):
    printer = await _make_printer(session_factory, release_required=True)
    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason == "pin_release"


async def test_resolve_hold_reason_follow_me_when_enabled(session_factory):
    printer = await _make_printer(session_factory, follow_me_enabled=True)
    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason == "follow_me"


async def test_resolve_hold_reason_follow_me_wins_over_pin_release(session_factory):
    printer = await _make_printer(session_factory, follow_me_enabled=True, release_required=True)
    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason == "follow_me"


async def test_resolve_hold_reason_none_when_quotas_disabled(session_factory):
    printer = await _make_printer(session_factory)
    now = datetime(2026, 7, 15, tzinfo=UTC)
    await _make_job(session_factory, printer.id, "matt@example.org", 500, now)
    async with session_factory() as session:
        session.add(
            PrinterUserQuota(
                printer_id=printer.id, user_email="matt@example.org", period="monthly", page_limit=1
            )
        )
        session.add(QuotaSettings(enabled=False))
        await session.commit()

    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason is None


async def test_resolve_hold_reason_quota_when_enabled_and_over_limit(session_factory):
    printer = await _make_printer(session_factory)
    now = datetime.now(UTC)
    await _make_job(session_factory, printer.id, "matt@example.org", 50, now)
    async with session_factory() as session:
        session.add(
            PrinterUserQuota(
                printer_id=printer.id,
                user_email="matt@example.org",
                period="monthly",
                page_limit=50,
            )
        )
        session.add(QuotaSettings(enabled=True))
        await session.commit()

    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason == "quota"


async def test_resolve_hold_reason_none_when_under_limit(session_factory):
    printer = await _make_printer(session_factory)
    now = datetime.now(UTC)
    await _make_job(session_factory, printer.id, "matt@example.org", 10, now)
    async with session_factory() as session:
        session.add(
            PrinterUserQuota(
                printer_id=printer.id,
                user_email="matt@example.org",
                period="monthly",
                page_limit=50,
            )
        )
        session.add(QuotaSettings(enabled=True))
        await session.commit()

    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, "matt@example.org")
        assert reason is None


async def test_resolve_hold_reason_none_without_submitted_by(session_factory):
    printer = await _make_printer(session_factory)
    async with session_factory() as session:
        session.add(QuotaSettings(enabled=True))
        await session.commit()
    async with session_factory() as session:
        reason = await resolve_hold_reason(session, printer, None)
        assert reason is None
