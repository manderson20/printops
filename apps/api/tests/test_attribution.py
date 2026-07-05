from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.attribution.resolve import resolve_user
from app.core.crypto import encrypt
from app.integrations.classguard import ClassGuardClient, ClassGuardError
from app.models.attribution_alias import AttributionAlias
from app.models.base import Base
from app.models.classguard import ClassGuardSettings
from app.models.device_override import DeviceUserOverride
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceSettings, GoogleWorkspaceUser
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
async def test_trusts_email_shaped_cups_user_immediately(db_session_factory):
    """An email-shaped CUPS username is assumed unambiguous and wins
    outright, without even attempting MAC resolution (mac_address is None
    in the returned tuple)."""
    async with db_session_factory() as db:
        user, method, mac = await resolve_user(db, "jdoe@example.com", "10.0.0.5")
    assert (user, method, mac) == ("jdoe@example.com", "cups", None)


@pytest.mark.asyncio
async def test_bare_cups_user_falls_back_to_cups_when_mac_unresolved(db_session_factory):
    """A bare, non-email-shaped CUPS username (e.g. a local macOS account
    name) is only trusted as a last resort — here MAC-based resolution
    can't run at all (no ClassGuard configured), so it still falls back
    to the raw value rather than "unknown"."""
    async with db_session_factory() as db:
        user, method, mac = await resolve_user(db, "jdoe", "10.0.0.5")
    assert (user, method, mac) == ("jdoe", "cups", None)


@pytest.mark.asyncio
async def test_generic_cups_user_falls_through(db_session_factory):
    # "anonymous" doesn't count as a trusted attribution (falls through
    # strategy 1), but since nothing else resolves it either, the raw
    # value is still surfaced rather than replaced with "unknown" —
    # only a wholly missing/empty submitted_by becomes "unknown".
    async with db_session_factory() as db:
        user, method, mac = await resolve_user(db, "anonymous", None)
    assert (user, method, mac) == ("anonymous", "unresolved", None)


@pytest.mark.asyncio
async def test_missing_cups_user_and_no_source_host_is_unresolved(db_session_factory):
    async with db_session_factory() as db:
        user, method, mac = await resolve_user(db, None, None)
    assert (user, method, mac) == ("unknown", "unresolved", None)


@pytest.mark.asyncio
async def test_mosyle_lookup_skipped_when_disabled(db_session_factory):
    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=False))
        await db.commit()
        user, method, mac = await resolve_user(db, "", "10.0.0.5")
    assert (user, method, mac) == ("unknown", "unresolved", None)


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
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("unknown", "unresolved", None)


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
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("unknown", "unresolved", None)


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
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("jdoe@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_mac_resolution_wins_over_ambiguous_bare_cups_username(db_session_factory, monkeypatch):
    """The actual bug this precedence change fixes: a bare local username
    like "matt" is shared by multiple people, so MAC-based MDM resolution
    must be tried first rather than trusting "matt" outright."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="matt.anderson@example.com",
                user_name="matt",
                synced_at=datetime.now(UTC),
            )
        )
        await db.commit()
        user, method, mac = await resolve_user(db, "matt", "10.0.0.5")
    assert (user, method, mac) == ("matt.anderson@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_device_override_takes_priority_over_mosyle(db_session_factory, monkeypatch):
    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="wrong-matt@example.com",
                user_name="matt",
                synced_at=datetime.now(UTC),
            )
        )
        db.add(
            DeviceUserOverride(mac_address="AA:BB:CC:DD:EE:FF", resolved_email="correct-matt@example.com")
        )
        await db.commit()
        user, method, mac = await resolve_user(db, "matt", "10.0.0.5")
    assert (user, method, mac) == ("correct-matt@example.com", "override", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_classguard_failure_falls_through_without_raising(db_session_factory, monkeypatch):
    async def fake_lookup_mac(self, ip):
        raise ClassGuardError("simulated outage")

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("unknown", "unresolved", None)


@pytest.mark.asyncio
async def test_unknown_mac_falls_through_to_unresolved(db_session_factory, monkeypatch):
    """ClassGuard resolves a MAC, but it's not in the Mosyle cache (e.g. a
    personal/unmanaged device) — still resolves to unresolved, not an
    error, and definitely not attributed to the wrong person. The MAC
    itself is still returned so it can be persisted on the Job row."""

    async def fake_lookup_mac(self, ip):
        return "11:22:33:44:55:66"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("unknown", "unresolved", "11:22:33:44:55:66")


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
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("student@example.com", "google_workspace", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_mosyle_email_confirmed_by_roster_wins_over_raw_value(db_session_factory, monkeypatch):
    """Roster has the same address but in a different case — the roster's
    canonical casing is what should be attributed, confirming the roster
    lookup actually ran rather than just trusting Mosyle's raw string."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="Jane.Doe@Example.com",
                synced_at=datetime.now(UTC),
            )
        )
        db.add(GoogleWorkspaceUser(email="jane.doe@example.com", name="Jane Doe", synced_at=datetime.now(UTC)))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("jane.doe@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_mosyle_username_reconciles_stale_email_against_roster(db_session_factory, monkeypatch):
    """The real bug this reconciliation fixes: Mosyle's reported email is
    a stale/wrong alias, but its separately-reported username's local
    part uniquely matches a real Workspace roster address — that roster
    address should be trusted over Mosyle's stale email."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="stale-alias@old-domain.example",
                user_name="jdoe",
                synced_at=datetime.now(UTC),
            )
        )
        db.add(GoogleWorkspaceUser(email="jdoe@example.com", name="Jane Doe", synced_at=datetime.now(UTC)))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("jdoe@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_mosyle_ambiguous_username_falls_back_to_raw_email(db_session_factory, monkeypatch):
    """Two roster users share the same local part under different
    domains — reconciliation must not guess; Mosyle's raw reported email
    is used as-is, same as if the roster didn't exist."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email="mosyle-reported@example.com",
                user_name="jdoe",
                synced_at=datetime.now(UTC),
            )
        )
        db.add(GoogleWorkspaceUser(email="jdoe@students.example.com", name="J1", synced_at=datetime.now(UTC)))
        db.add(GoogleWorkspaceUser(email="jdoe@staff.example.com", name="J2", synced_at=datetime.now(UTC)))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("mosyle-reported@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_mosyle_username_only_reconciles_against_roster(db_session_factory, monkeypatch):
    """Mosyle has no email at all for this device (only a username) — the
    roster lookup by local part should still be enough to attribute it."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(MosyleSettings(enabled=True))
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                user_email=None,
                user_name="jdoe",
                synced_at=datetime.now(UTC),
            )
        )
        db.add(GoogleWorkspaceUser(email="jdoe@example.com", name="Jane Doe", synced_at=datetime.now(UTC)))
        await db.commit()
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("jdoe@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


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
        user, method, mac = await resolve_user(db, None, "10.0.0.5")
    assert (user, method, mac) == ("mosyle-user@example.com", "mosyle", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_bare_username_alias_resolves_without_any_mac_lookup(db_session_factory):
    """The "matt" scenario: a bare local username with no ClassGuard/MAC
    machinery configured at all still resolves via a manual attribution
    alias (app/routers/attribution_aliases.py)."""
    async with db_session_factory() as db:
        db.add(AttributionAlias(alias="matt", resolved_email="manderson@brookfieldr3.org", source="manual"))
        await db.commit()
        user, method, mac = await resolve_user(db, "matt", None)
    assert (user, method, mac) == ("manderson@brookfieldr3.org", "alias", None)


@pytest.mark.asyncio
async def test_email_shaped_alias_resolves_immediately_no_mac_lookup(db_session_factory, monkeypatch):
    """A Google Workspace-synced alias email (an old/renamed address) is
    caught before the normal "any email wins outright" rule — and never
    triggers a ClassGuard lookup, matching the existing email-shaped-value
    fast path."""

    async def fail_if_called(self, ip):
        raise AssertionError("ClassGuard should never be queried for an alias-resolved email")

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fail_if_called)

    async with db_session_factory() as db:
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(
            AttributionAlias(
                alias="manderson.old@brookfieldr3.org",
                resolved_email="manderson@brookfieldr3.org",
                source="google_workspace_sync",
            )
        )
        await db.commit()
        user, method, mac = await resolve_user(db, "manderson.old@brookfieldr3.org", "10.0.0.5")
    assert (user, method, mac) == ("manderson@brookfieldr3.org", "alias", None)


@pytest.mark.asyncio
async def test_device_override_still_wins_over_username_alias(db_session_factory, monkeypatch):
    """A device-specific MAC override is more precise than a general
    "this string always means X" alias, and is checked first for the
    bare-username fallback case."""

    async def fake_lookup_mac(self, ip):
        return "aa:bb:cc:dd:ee:ff"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    async with db_session_factory() as db:
        db.add(ClassGuardSettings(enabled=True, access_token_encrypted=encrypt("tok")))
        db.add(DeviceUserOverride(mac_address="AA:BB:CC:DD:EE:FF", resolved_email="override@example.com"))
        db.add(AttributionAlias(alias="matt", resolved_email="manderson@brookfieldr3.org", source="manual"))
        await db.commit()
        user, method, mac = await resolve_user(db, "matt", "10.0.0.5")
    assert (user, method, mac) == ("override@example.com", "override", "AA:BB:CC:DD:EE:FF")


@pytest.mark.asyncio
async def test_no_matching_alias_falls_back_to_raw_cups_value(db_session_factory):
    async with db_session_factory() as db:
        db.add(AttributionAlias(alias="someone-else", resolved_email="x@example.com", source="manual"))
        await db.commit()
        user, method, mac = await resolve_user(db, "matt", None)
    assert (user, method, mac) == ("matt", "cups", None)
