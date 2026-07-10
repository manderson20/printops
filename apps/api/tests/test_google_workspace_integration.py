import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.integrations.google_workspace import (
    _refresh_google_sourced_aliases,
    _refresh_google_sourced_copier_identities,
    extract_employee_id,
    normalize_org_unit_path,
    org_unit_matches,
)
from app.models.attribution_alias import AttributionAlias
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceSettings
from app.models.job import Job
from app.models.printer import Printer
from app.models.staff_copier_identity import StaffCopierIdentity


def test_extract_employee_id_present():
    user = {"externalIds": [{"type": "organization", "value": "10023"}]}
    assert extract_employee_id(user) == "10023"


def test_extract_employee_id_missing_external_ids():
    assert extract_employee_id({}) is None


def test_extract_employee_id_empty_external_ids():
    assert extract_employee_id({"externalIds": []}) is None


def test_extract_employee_id_wrong_type_only():
    user = {"externalIds": [{"type": "custom", "customType": "badge", "value": "X-1"}]}
    assert extract_employee_id(user) is None


def test_extract_employee_id_picks_organization_among_multiple():
    user = {
        "externalIds": [
            {"type": "custom", "customType": "badge", "value": "X-1"},
            {"type": "organization", "value": "10023"},
        ]
    }
    assert extract_employee_id(user) == "10023"


def test_extract_employee_id_ignores_blank_value():
    user = {"externalIds": [{"type": "organization", "value": ""}]}
    assert extract_employee_id(user) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/Employees", "/Employees"),
        ("/Employees/", "/Employees"),
        (" /Employees ", "/Employees"),
        ("Employees", "/Employees"),
        ("/", "/"),
    ],
)
def test_normalize_org_unit_path(raw, expected):
    assert normalize_org_unit_path(raw) == expected


def test_org_unit_matches_exact():
    assert org_unit_matches("/Employees", "/Employees") is True


def test_org_unit_matches_nested_sub_ou():
    assert org_unit_matches("/Employees/Teachers", "/Employees") is True


def test_org_unit_matches_rejects_similarly_named_ou():
    # A naive prefix/LIKE match would incorrectly treat this as nested.
    assert org_unit_matches("/EmployeesOld", "/Employees") is False


def test_org_unit_matches_rejects_unrelated_ou():
    assert org_unit_matches("/Students", "/Employees") is False


def test_org_unit_matches_none_path():
    assert org_unit_matches(None, "/Employees") is False


def test_org_unit_matches_tolerates_trailing_slash_on_setting():
    assert org_unit_matches("/Employees/Teachers", "/Employees/") is True


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
async def test_refresh_aliases_populates_from_google_and_backfills_jobs(db_session_factory):
    async with db_session_factory() as db:
        printer = Printer(name="Test Printer", ip_address="10.0.0.9")
        db.add(printer)
        await db.flush()
        db.add(
            Job(printer_id=printer.id, submitted_by="old.address@district.org", status="forwarded")
        )
        await db.commit()

        users = [
            {"primaryEmail": "new.address@district.org", "aliases": ["old.address@district.org"]}
        ]
        await _refresh_google_sourced_aliases(db, users)
        await db.commit()

        result = await db.execute(select(AttributionAlias))
        aliases = result.scalars().all()
        assert len(aliases) == 1
        assert aliases[0].alias == "old.address@district.org"
        assert aliases[0].resolved_email == "new.address@district.org"
        assert aliases[0].source == "google_workspace_sync"

        job_result = await db.execute(select(Job))
        job = job_result.scalar_one()
        assert job.submitted_by == "new.address@district.org"
        assert job.attribution_method == "alias"


@pytest.mark.asyncio
async def test_refresh_aliases_never_overwrites_manual_alias(db_session_factory):
    async with db_session_factory() as db:
        db.add(
            AttributionAlias(alias="matt", resolved_email="manderson@district.org", source="manual")
        )
        await db.commit()

        # Google independently reports "matt" as an alias of a different account.
        users = [{"primaryEmail": "someone.else@district.org", "aliases": ["matt"]}]
        await _refresh_google_sourced_aliases(db, users)
        await db.commit()

        result = await db.execute(select(AttributionAlias))
        aliases = result.scalars().all()
        assert len(aliases) == 1
        assert aliases[0].source == "manual"
        assert aliases[0].resolved_email == "manderson@district.org"


@pytest.mark.asyncio
async def test_refresh_aliases_drops_ambiguous_alias_claimed_by_two_accounts(db_session_factory):
    async with db_session_factory() as db:
        users = [
            {"primaryEmail": "person.a@district.org", "aliases": ["shared@district.org"]},
            {"primaryEmail": "person.b@district.org", "aliases": ["shared@district.org"]},
        ]
        await _refresh_google_sourced_aliases(db, users)
        await db.commit()

        result = await db.execute(select(AttributionAlias))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_refresh_copier_identities_creates_when_enabled(db_session_factory):
    async with db_session_factory() as db:
        settings = GoogleWorkspaceSettings(
            auto_create_copier_identity_from_employee_id=True, auto_copier_identity_type="staff_id"
        )
        await _refresh_google_sourced_copier_identities(
            db, [("jane.smith@district.org", "12345")], settings
        )
        await db.commit()

        result = await db.execute(select(StaffCopierIdentity))
        identities = result.scalars().all()
        assert len(identities) == 1
        assert identities[0].staff_email == "jane.smith@district.org"
        assert identities[0].identity_type == "staff_id"
        assert identities[0].identity_value == "12345"
        assert identities[0].source == "google_workspace_sync"


@pytest.mark.asyncio
async def test_refresh_copier_identities_removes_all_when_disabled(db_session_factory):
    async with db_session_factory() as db:
        db.add(
            StaffCopierIdentity(
                staff_email="jane.smith@district.org",
                identity_type="staff_id",
                identity_value="12345",
                source="google_workspace_sync",
            )
        )
        await db.commit()

        settings = GoogleWorkspaceSettings(
            auto_create_copier_identity_from_employee_id=False, auto_copier_identity_type="staff_id"
        )
        await _refresh_google_sourced_copier_identities(db, [], settings)
        await db.commit()

        result = await db.execute(select(StaffCopierIdentity))
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_refresh_copier_identities_does_not_override_manual_claim(db_session_factory):
    async with db_session_factory() as db:
        # An admin already manually assigned "12345" to a different person.
        db.add(
            StaffCopierIdentity(
                staff_email="someone.else@district.org",
                identity_type="staff_id",
                identity_value="12345",
                source="manual",
            )
        )
        await db.commit()

        settings = GoogleWorkspaceSettings(
            auto_create_copier_identity_from_employee_id=True, auto_copier_identity_type="staff_id"
        )
        await _refresh_google_sourced_copier_identities(
            db, [("jane.smith@district.org", "12345")], settings
        )
        await db.commit()

        result = await db.execute(select(StaffCopierIdentity))
        identities = result.scalars().all()
        # Only the manual row exists — the sync did not add a conflicting one.
        assert len(identities) == 1
        assert identities[0].source == "manual"
        assert identities[0].staff_email == "someone.else@district.org"
