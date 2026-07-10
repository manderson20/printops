import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.integrations.classguard import ClassGuardClient
from app.integrations.google_workspace import GoogleWorkspaceClient
from app.integrations.mosyle import MosyleClient
from app.main import app
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceUser


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def client(db_session_factory):
    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_settings_requires_auth(client):
    response = client.get("/api/v1/settings/mosyle")
    assert response.status_code == 401


def test_get_settings_creates_default_row(client, auth_headers):
    response = client.get("/api/v1/settings/mosyle", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["has_access_token"] is False
    assert body["has_admin_password"] is False


def test_update_settings_sets_secrets_without_exposing_them(client, auth_headers):
    response = client.put(
        "/api/v1/settings/mosyle",
        headers=auth_headers,
        json={
            "base_url": "https://businessapi.mosyle.com/v1",
            "access_token": "super-secret-token",
            "admin_email": "admin@example.com",
            "admin_password": "super-secret-password",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_access_token"] is True
    assert body["has_admin_password"] is True
    assert body["admin_email"] == "admin@example.com"
    assert "super-secret-token" not in response.text
    assert "super-secret-password" not in response.text


def test_update_settings_omitted_secret_keeps_previous_value(client, auth_headers, monkeypatch):
    client.put(
        "/api/v1/settings/mosyle",
        headers=auth_headers,
        json={"access_token": "first-token", "admin_email": "a@x.com", "admin_password": "pw"},
    )
    # Edit again without touching the secret fields — should not clear them.
    response = client.put(
        "/api/v1/settings/mosyle",
        headers=auth_headers,
        json={"base_url": "https://other.example/v1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_access_token"] is True
    assert body["has_admin_password"] is True
    assert body["base_url"] == "https://other.example/v1"


def test_test_connection_success(client, auth_headers, monkeypatch):
    async def fake_list_devices(self, os="mac"):
        return [{"serial_number": "SN1"}, {"serial_number": "SN2"}]

    monkeypatch.setattr(MosyleClient, "list_devices", fake_list_devices)

    response = client.post(
        "/api/v1/settings/mosyle/test",
        headers=auth_headers,
        json={
            "base_url": "https://businessapi.mosyle.com/v1",
            "access_token": "tok",
            "admin_email": "admin@example.com",
            "admin_password": "pw",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["device_count"] == 2


def test_test_connection_missing_fields(client, auth_headers):
    response = client.post("/api/v1/settings/mosyle/test", headers=auth_headers, json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "required" in body["error"]


def test_sync_endpoint_surfaces_failure_as_502(client, auth_headers):
    # Nothing configured yet -> sync_devices raises "not configured/enabled".
    response = client.post("/api/v1/settings/mosyle/sync", headers=auth_headers)
    assert response.status_code == 502


def test_get_classguard_settings_creates_default_row(client, auth_headers):
    response = client.get("/api/v1/settings/classguard", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["has_access_token"] is False
    assert body["base_url"] == ""


def test_update_classguard_settings_hides_token(client, auth_headers):
    response = client.put(
        "/api/v1/settings/classguard",
        headers=auth_headers,
        json={"access_token": "super-secret-token", "enabled": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_access_token"] is True
    assert body["enabled"] is True
    assert "super-secret-token" not in response.text


def test_classguard_test_connection_hit(client, auth_headers, monkeypatch):
    async def fake_lookup_mac(self, ip):
        assert ip == "10.0.0.42"
        return "AA:BB:CC:DD:EE:FF"

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    response = client.post(
        "/api/v1/settings/classguard/test",
        headers=auth_headers,
        json={
            "base_url": "https://classguard.example.org",
            "access_token": "tok",
            "test_ip": "10.0.0.42",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mac_address"] == "AA:BB:CC:DD:EE:FF"


def test_classguard_test_connection_no_lease_still_ok(client, auth_headers, monkeypatch):
    async def fake_lookup_mac(self, ip):
        return None

    monkeypatch.setattr(ClassGuardClient, "lookup_mac", fake_lookup_mac)

    response = client.post(
        "/api/v1/settings/classguard/test",
        headers=auth_headers,
        json={
            "base_url": "https://classguard.example.org",
            "access_token": "tok",
            "test_ip": "10.0.0.42",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mac_address"] is None


def test_classguard_test_connection_missing_fields(client, auth_headers):
    response = client.post(
        "/api/v1/settings/classguard/test", headers=auth_headers, json={"test_ip": "10.0.0.42"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "required" in body["error"]


FAKE_SERVICE_ACCOUNT_JSON = json.dumps(
    {"client_email": "svc@project.iam.gserviceaccount.com", "private_key": "fake"}
)


def test_get_google_workspace_settings_creates_default_row(client, auth_headers):
    response = client.get("/api/v1/settings/google-workspace", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["has_service_account_json"] is False
    assert body["customer_id"] == "my_customer"


def test_update_google_workspace_settings_hides_key(client, auth_headers):
    response = client.put(
        "/api/v1/settings/google-workspace",
        headers=auth_headers,
        json={
            "service_account_json": FAKE_SERVICE_ACCOUNT_JSON,
            "admin_email": "admin@example.com",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_service_account_json"] is True
    assert body["admin_email"] == "admin@example.com"
    assert "svc@project.iam.gserviceaccount.com" not in response.text


def test_google_workspace_test_connection_success(client, auth_headers, monkeypatch):
    async def fake_list_devices(self):
        return [{"serialNumber": "SN1"}, {"serialNumber": "SN2"}, {"serialNumber": "SN3"}]

    monkeypatch.setattr(GoogleWorkspaceClient, "list_chromeos_devices", fake_list_devices)

    response = client.post(
        "/api/v1/settings/google-workspace/test",
        headers=auth_headers,
        json={
            "service_account_json": FAKE_SERVICE_ACCOUNT_JSON,
            "admin_email": "admin@example.com",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["device_count"] == 3


def test_google_workspace_test_connection_missing_fields(client, auth_headers):
    response = client.post("/api/v1/settings/google-workspace/test", headers=auth_headers, json={})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "required" in body["error"]


def test_google_workspace_test_connection_invalid_json(client, auth_headers):
    response = client.post(
        "/api/v1/settings/google-workspace/test",
        headers=auth_headers,
        json={"service_account_json": "not json", "admin_email": "admin@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not valid JSON" in body["error"]


def test_google_workspace_sync_endpoint_surfaces_failure_as_502(client, auth_headers):
    response = client.post("/api/v1/settings/google-workspace/sync", headers=auth_headers)
    assert response.status_code == 502


def test_copier_pin_roster_requires_auth(client):
    response = client.get("/api/v1/settings/google-workspace/copier-pin-roster.csv")
    assert response.status_code == 401


async def test_copier_pin_roster_includes_only_users_with_employee_id(
    client, auth_headers, db_session_factory
):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="alice@example.com",
                name="Alice",
                employee_id="1001",
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="bob@example.com", name="Bob", employee_id=None, synced_at=datetime.now(UTC)
            )
        )
        await session.commit()

    response = client.get(
        "/api/v1/settings/google-workspace/copier-pin-roster.csv", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.text
    assert body.splitlines()[0] == "Name,Email,PIN"
    assert "Alice,alice@example.com,1001" in body
    assert "bob@example.com" not in body


async def test_copier_pin_roster_unfiltered_without_staff_ou_setting(
    client, auth_headers, db_session_factory
):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="teacher@example.com",
                name="Teacher",
                employee_id="1001",
                org_unit_path="/Employees",
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="student@example.com",
                name="Student",
                employee_id="9001",
                org_unit_path="/Students",
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = client.get(
        "/api/v1/settings/google-workspace/copier-pin-roster.csv", headers=auth_headers
    )
    body = response.text
    assert "teacher@example.com" in body
    assert "student@example.com" in body


async def test_copier_pin_roster_filters_by_configured_staff_ou(
    client, auth_headers, db_session_factory
):
    client.put(
        "/api/v1/settings/google-workspace",
        headers=auth_headers,
        json={"staff_org_unit_path": "/Employees"},
    )
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="teacher@example.com",
                name="Teacher",
                employee_id="1001",
                org_unit_path="/Employees/Teachers",
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="student@example.com",
                name="Student",
                employee_id="9001",
                org_unit_path="/Students",
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = client.get(
        "/api/v1/settings/google-workspace/copier-pin-roster.csv", headers=auth_headers
    )
    body = response.text
    assert "teacher@example.com" in body
    assert "student@example.com" not in body


async def test_org_units_endpoint_filters_by_configured_staff_ou(
    client, auth_headers, db_session_factory
):
    client.put(
        "/api/v1/settings/google-workspace",
        headers=auth_headers,
        json={"staff_org_unit_path": "/Employees"},
    )
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="teacher@example.com",
                name="Teacher",
                org_unit_path="/Employees/Teachers",
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="student@example.com",
                name="Student",
                org_unit_path="/Students/High School",
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = client.get("/api/v1/settings/google-workspace/org-units", headers=auth_headers)
    assert response.status_code == 200
    org_units = response.json()
    assert "/Employees/Teachers" in org_units
    assert "/Students/High School" not in org_units


async def test_org_units_endpoint_unfiltered_when_staff_ou_not_configured(
    client, auth_headers, db_session_factory
):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="teacher@example.com",
                name="Teacher",
                org_unit_path="/Employees/Teachers",
                synced_at=datetime.now(UTC),
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="student@example.com",
                name="Student",
                org_unit_path="/Students/High School",
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()

    response = client.get("/api/v1/settings/google-workspace/org-units", headers=auth_headers)
    assert response.status_code == 200
    org_units = response.json()
    assert "/Employees/Teachers" in org_units
    assert "/Students/High School" in org_units


def test_staff_org_unit_path_can_be_cleared(client, auth_headers):
    set_response = client.put(
        "/api/v1/settings/google-workspace",
        headers=auth_headers,
        json={"staff_org_unit_path": "/Employees"},
    )
    assert set_response.json()["staff_org_unit_path"] == "/Employees"

    cleared = client.put(
        "/api/v1/settings/google-workspace",
        headers=auth_headers,
        json={"staff_org_unit_path": ""},
    )
    assert cleared.json()["staff_org_unit_path"] is None


def test_get_snmp_defaults_creates_default_row(client, auth_headers):
    response = client.get("/api/v1/settings/snmp", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    # Seeded with "public" (this district's confirmed-working default) but
    # left disabled until an admin explicitly opts in.
    assert body["has_community"] is True
    assert body["enabled"] is False
    assert body["version"] == "v2c"
    assert body["port"] == 161
    assert body["retention_days"] == 180


def test_update_snmp_defaults_hides_community(client, auth_headers):
    response = client.put(
        "/api/v1/settings/snmp",
        headers=auth_headers,
        json={
            "community": "super-secret-community",
            "enabled": True,
            "version": "v1",
            "port": 1610,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_community"] is True
    assert body["enabled"] is True
    assert body["version"] == "v1"
    assert body["port"] == 1610
    assert "super-secret-community" not in response.text


def test_update_snmp_defaults_requires_admin(client):
    response = client.put("/api/v1/settings/snmp", json={"enabled": True})
    assert response.status_code == 401


def test_snmp_retention_days_round_trips(client, auth_headers):
    response = client.put(
        "/api/v1/settings/snmp",
        headers=auth_headers,
        json={"retention_days": 90},
    )
    assert response.status_code == 200
    assert response.json()["retention_days"] == 90

    fetched = client.get("/api/v1/settings/snmp", headers=auth_headers)
    assert fetched.json()["retention_days"] == 90
