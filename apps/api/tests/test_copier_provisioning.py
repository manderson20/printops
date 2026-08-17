"""Choosing who gets an account on a copier, and the Konica sync's
behaviour when the device pushes back."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.copiers.connector import CapabilityNotSupported
from app.copiers.konica_admin import KonicaAdminError, TrackAccount
from app.copiers.konica_bizhub import KonicaBizhubConnector
from app.copiers.provisioning import build_provisioning_plan
from app.core.crypto import encrypt
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.mfp_device import MfpDevice
from app.models.staff_copier_identity import StaffCopierIdentity


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


async def _seed(db, people):
    """people: (email, employee_id, org_unit_path)"""
    db.add(
        GoogleWorkspaceSettings(
            staff_org_unit_path="/Employees",
            auto_copier_identity_type="staff_id",
            copier_identity_excluded_org_unit_paths=["/Employees/Inactive Employees"],
        )
    )
    for email, employee_id, ou in people:
        db.add(GoogleWorkspaceUser(email=email, org_unit_path=ou, synced_at=_now()))
        db.add(
            StaffCopierIdentity(
                staff_email=email,
                identity_type="staff_id",
                identity_value=employee_id,
                source="google_workspace_sync",
            )
        )
    await db.commit()


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


PEOPLE = [
    ("teacher@d.org", "10001", "/Employees/Elementary School"),
    ("hs@d.org", "10002", "/Employees/High School"),
    ("former@d.org", "10003", "/Employees/Inactive Employees"),
    ("student@d.org", "10004", "/Students/3rd Grade"),
]


@pytest.mark.asyncio
async def test_plan_uses_org_wide_scope_when_device_is_unscoped(db):
    await _seed(db, PEOPLE)
    device = MfpDevice(name="Copier", connector_type="konica_bizhub")
    plan = await build_provisioning_plan(db, device)
    assert sorted(i.staff_email for i in plan.identities) == ["hs@d.org", "teacher@d.org"]


@pytest.mark.asyncio
async def test_device_scope_narrows_to_one_building(db):
    await _seed(db, PEOPLE)
    device = MfpDevice(
        name="ES Copier",
        connector_type="konica_bizhub",
        provision_org_unit_paths=["/Employees/Elementary School"],
    )
    plan = await build_provisioning_plan(db, device)
    assert [i.staff_email for i in plan.identities] == ["teacher@d.org"]


@pytest.mark.asyncio
async def test_identity_not_in_the_directory_is_skipped_and_counted(db):
    """Provisioning a code PrintOps can't attribute to a real person would
    produce usage nobody can resolve — so it's skipped, and surfaced."""
    await _seed(db, PEOPLE)
    db.add(
        StaffCopierIdentity(
            staff_email="ghost@d.org", identity_type="staff_id", identity_value="10099"
        )
    )
    await db.commit()

    plan = await build_provisioning_plan(db, MfpDevice(name="C", connector_type="konica_bizhub"))
    assert "ghost@d.org" not in [i.staff_email for i in plan.identities]
    assert plan.skipped_no_org_unit == 1


@pytest.mark.asyncio
async def test_duplicate_codes_are_not_pushed(db):
    """A code held by two people can't be attributed either way."""
    await _seed(db, PEOPLE)
    db.add(GoogleWorkspaceUser(email="twin@d.org", org_unit_path="/Employees", synced_at=_now()))
    db.add(
        StaffCopierIdentity(
            staff_email="twin@d.org", identity_type="staff_id", identity_value="10001"
        )
    )
    await db.commit()

    plan = await build_provisioning_plan(db, MfpDevice(name="C", connector_type="konica_bizhub"))
    values = [i.identity_value for i in plan.identities]
    assert values.count("10001") == 1


class _FakeSession:
    """Stands in for KonicaAdminSession — the HTTP layer is captured in
    docs/copier-capture-konica.md and exercised against real hardware; what
    needs testing here is how the connector reacts."""

    def __init__(self, existing=None, fail_on=None, list_error=None):
        self.existing = existing or []
        self.fail_on = fail_on or set()
        self.list_error = list_error
        self.created: list[tuple[str, str, str]] = []
        self.logged_out = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.logged_out = True

    async def list_accounts(self, limit=1000):
        if self.list_error:
            raise self.list_error
        return self.existing

    async def create_account(self, track_number, name, password, registration_max=1000):
        if password in self.fail_on:
            raise KonicaAdminError("GeneralIllegalValue (TrackName)")
        self.created.append((track_number, name, password))


def _identity(email, value):
    return StaffCopierIdentity(staff_email=email, identity_type="staff_id", identity_value=value)


def _device():
    return MfpDevice(
        name="ES Copier",
        connector_type="konica_bizhub",
        ip_address="192.0.2.10",
        admin_password_encrypted=encrypt("pw"),
    )


@pytest.mark.asyncio
async def test_sync_reports_account_track_off_as_actionable(monkeypatch):
    """AuthNotTrackMode is a setup problem with a fix an admin can carry
    out, not a server error."""
    fake = _FakeSession(list_error=KonicaAdminError("AuthNotTrackMode invalid track mode"))
    monkeypatch.setattr("app.copiers.konica_bizhub.KonicaAdminSession", lambda ip, creds: fake)
    with pytest.raises(CapabilityNotSupported) as excinfo:
        await KonicaBizhubConnector().sync_users_to_device(_device(), [_identity("a@d.org", "1")])
    assert "Account Track is switched off" in str(excinfo.value)


@pytest.mark.asyncio
async def test_sync_creates_accounts_and_skips_used_numbers(monkeypatch):
    fake = _FakeSession(existing=[TrackAccount("1", None, True, False)])
    monkeypatch.setattr("app.copiers.konica_bizhub.KonicaAdminSession", lambda ip, creds: fake)
    result = await KonicaBizhubConnector().sync_users_to_device(
        _device(), [_identity("amy@d.org", "10001"), _identity("bob@d.org", "10002")]
    )
    assert result.synced_count == 2
    assert result.failed_count == 0
    # Account number 1 was taken, so the new ones start at 2.
    assert [n for n, _, _ in fake.created] == ["2", "3"]
    # The panel label comes from the email local part, capped at 8 chars.
    assert [name for _, name, _ in fake.created] == ["amy", "bob"]
    assert [pw for _, _, pw in fake.created] == ["10001", "10002"]


@pytest.mark.asyncio
async def test_one_rejected_account_does_not_abandon_the_rest(monkeypatch):
    fake = _FakeSession(fail_on={"10002"})
    monkeypatch.setattr("app.copiers.konica_bizhub.KonicaAdminSession", lambda ip, creds: fake)
    result = await KonicaBizhubConnector().sync_users_to_device(
        _device(),
        [
            _identity("a@d.org", "10001"),
            _identity("b@d.org", "10002"),
            _identity("c@d.org", "10003"),
        ],
    )
    assert result.synced_count == 2
    assert result.failed_count == 1
    assert "b@d.org" in result.message


@pytest.mark.asyncio
async def test_long_email_local_part_is_truncated_to_the_device_limit(monkeypatch):
    """AA_TRA_T_NAM is capped at 8 characters by the device; overrunning it
    is what the device rejects with GeneralIllegalValue(TrackName)."""
    fake = _FakeSession()
    monkeypatch.setattr("app.copiers.konica_bizhub.KonicaAdminSession", lambda ip, creds: fake)
    await KonicaBizhubConnector().sync_users_to_device(
        _device(), [_identity("verylongname@d.org", "10001")]
    )
    assert fake.created[0][1] == "verylong"
    assert len(fake.created[0][1]) <= 8


@pytest.mark.asyncio
async def test_missing_admin_password_is_reported_not_crashed():
    device = MfpDevice(name="No creds", connector_type="konica_bizhub", ip_address="192.0.2.11")
    from app.copiers.device_admin import DeviceCredentialsMissing

    with pytest.raises(DeviceCredentialsMissing):
        await KonicaBizhubConnector().sync_users_to_device(device, [])
