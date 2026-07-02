from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.attribution.resolve import resolve_user
from app.models.base import Base
from app.models.mosyle import MosyleDevice, MosyleSettings


@pytest_asyncio.fixture
async def db_session_factory():
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


@pytest.mark.asyncio
async def test_trusts_present_cups_user(db_session_factory):
    async with db_session_factory() as db:
        user, method = await resolve_user(db, "jdoe", "10.0.0.5")
    assert (user, method) == ("jdoe", "cups")


@pytest.mark.asyncio
async def test_generic_cups_user_falls_through(db_session_factory):
    # "anonymous" doesn't count as a trusted attribution (falls through
    # strategy 1), but since nothing else resolves it either, the raw
    # value is still surfaced rather than replaced with "unknown" —
    # only a wholly missing/empty submitted_by becomes "unknown".
    async with db_session_factory() as db:
        user, method = await resolve_user(db, "anonymous", None)
    assert (user, method) == ("anonymous", "unresolved")


@pytest.mark.asyncio
async def test_missing_cups_user_and_no_source_host_is_unresolved(db_session_factory):
    async with db_session_factory() as db:
        user, method = await resolve_user(db, None, None)
    assert (user, method) == ("unknown", "unresolved")


@pytest.mark.asyncio
async def test_mosyle_lookup_skipped_when_disabled(db_session_factory):
    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=False))
        await db.commit()
        user, method = await resolve_user(db, "", "10.0.0.5")
    assert (user, method) == ("unknown", "unresolved")


@pytest.mark.asyncio
async def test_mosyle_lookup_currently_never_resolves_pending_classguard(db_session_factory):
    """MAC lookup (_lookup_mac_for_source) is an intentionally unimplemented
    seam pending a ClassGuard integration — see its docstring. Even with
    Mosyle enabled and a matching device cached, nothing resolves via MAC
    today; this documents that current, expected behavior so a future
    ClassGuard wire-up is the thing that flips this test, not a silent
    regression."""
    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="jdoe@example.com",
                synced_at=datetime.now(UTC),
            )
        )
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("unknown", "unresolved")


@pytest.mark.asyncio
async def test_hostname_source_host_is_not_treated_as_ip(db_session_factory):
    from app.attribution.resolve import _lookup_mac_for_source

    assert _lookup_mac_for_source("some-mac.local") is None
