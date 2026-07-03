from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.attribution.resolve import resolve_user
from app.core.crypto import encrypt
from app.integrations.classguard import ClassGuardClient, ClassGuardError
from app.models.base import Base
from app.models.classguard import ClassGuardSettings
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceSettings
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
async def test_mac_lookup_unresolved_when_classguard_not_configured(db_session_factory):
    """Mosyle enabled + a matching cached device, but no ClassGuardSettings
    row at all — the MAC lookup has nothing to call, so this still falls
    through to unresolved rather than erroring."""
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
async def test_mac_lookup_unresolved_when_classguard_disabled(db_session_factory):
    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(
            ClassGuardSettings(enabled=False, access_token_encrypted=encrypt("tok")),
        )
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
async def test_resolves_via_classguard_mac_lookup(db_session_factory, monkeypatch):
    async def fake_lookup_mac(self, ip):
        assert ip == "10.0.0.5"
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="jdoe@example.com",
                synced_at=datetime.now(UTC),
            )
        )
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("jdoe@example.com", "mosyle")


@pytest.mark.asyncio
async def test_classguard_failure_falls_through_without_raising(db_session_factory, monkeypatch):
    async def fake_lookup_mac(self, ip):
        raise ClassGuardError("simulated outage")

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("unknown", "unresolved")


@pytest.mark.asyncio
async def test_unknown_mac_falls_through_to_unresolved(db_session_factory, monkeypatch):
    """ClassGuard resolves a MAC, but it's not in the Mosyle cache (e.g. a
    personal/unmanaged device) — still resolves to unresolved, not an
    error, and definitely not attributed to the wrong person."""

    async def fake_lookup_mac(self, ip):
        return "11:22:33:44:55:66"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("unknown", "unresolved")


@pytest.mark.asyncio
async def test_hostname_source_host_is_not_treated_as_ip(db_session_factory):
    from app.attribution.resolve import _lookup_mac_for_source

    async with db_session_factory() as db:
        assert await _lookup_mac_for_source(db, "some-mac.local") is None


@pytest.mark.asyncio
async def test_resolves_via_google_workspace_when_mosyle_misses(db_session_factory, monkeypatch):
    """Mosyle is enabled but has no matching device cached; Google
    Workspace is also enabled and does — strategy 3 should still resolve
    it, not just stop at strategy 2's miss."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))  # enabled, but no matching MosyleDevice row
        db.add(GoogleWorkspaceSettings(enabled=True, service_account_json_encrypted=encrypt("{}"), admin_email="a@x.com"))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            GoogleWorkspaceDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="student@example.com",
                synced_at=datetime.now(UTC),
            )
        )
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("student@example.com", "google_workspace")


@pytest.mark.asyncio
async def test_mosyle_takes_priority_over_google_workspace(db_session_factory, monkeypatch):
    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(GoogleWorkspaceSettings(enabled=True, service_account_json_encrypted=encrypt("{}"), admin_email="a@x.com"))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF", user_email="mosyle-user@example.com", synced_at=datetime.now(UTC)
            )
        )
        db.add(
            GoogleWorkspaceDevice(
                mac_address="AA:BB:CC:DD:EE:FF", user_email="gw-user@example.com", synced_at=datetime.now(UTC)
            )
        )
        await db.commit()
        user, method = await resolve_user(db, None, "10.0.0.5")
    assert (user, method) == ("mosyle-user@example.com", "mosyle")
