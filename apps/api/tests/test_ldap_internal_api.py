from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.printer import Printer


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
    async with session_factory() as seed:
        seed.add(
            GoogleWorkspaceUser(
                email="matt.anderson@example.com",
                name="Matt Anderson",
                employee_id="1001",
                synced_at=datetime.now(UTC),
            )
        )
        seed.add(
            GoogleWorkspaceUser(
                email="jane.smith@example.com",
                name="Jane Smith",
                employee_id="1002",
                synced_at=datetime.now(UTC),
            )
        )
        await seed.commit()
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
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture
async def enabled_printer(db_session_factory):
    """LDAP-enabled printer with a known bind credential, on a relay
    that's globally enabled — the "everything should work" baseline most
    tests start from."""
    async with db_session_factory() as session:
        printer = Printer(
            name="Front Office Copier",
            ip_address="10.0.0.20",
            ldap_enabled=True,
            ldap_bind_username="front-office-copier",
            ldap_bind_password_hash=hash_password("s3cret"),
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer


def _enable_relay(client, auth_headers):
    response = client.put("/api/v1/settings/ldap", headers=auth_headers, json={"enabled": True})
    assert response.status_code == 200


# --- bind ---


def test_bind_fails_when_relay_disabled_globally(client, enabled_printer, backend_headers):
    response = client.post(
        "/api/v1/internal/ldap/bind",
        headers=backend_headers,
        json={"bind_identifier": "front-office-copier", "password": "s3cret"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_bind_succeeds_with_correct_credentials(
    client, auth_headers, enabled_printer, backend_headers
):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/bind",
        headers=backend_headers,
        json={"bind_identifier": "Front-Office-Copier", "password": "s3cret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["printer_id"] == str(enabled_printer.id)


def test_bind_fails_with_wrong_password(client, auth_headers, enabled_printer, backend_headers):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/bind",
        headers=backend_headers,
        json={"bind_identifier": "front-office-copier", "password": "wrong"},
    )
    assert response.json()["success"] is False


def test_bind_fails_for_unknown_identifier(client, auth_headers, enabled_printer, backend_headers):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/bind",
        headers=backend_headers,
        json={"bind_identifier": "no-such-printer", "password": "s3cret"},
    )
    assert response.json()["success"] is False


async def test_bind_fails_when_printer_ldap_disabled(
    client, auth_headers, backend_headers, db_session_factory
):
    _enable_relay(client, auth_headers)
    async with db_session_factory() as session:
        printer = Printer(
            name="Disabled Copier",
            ip_address="10.0.0.21",
            ldap_enabled=False,
            ldap_bind_username="disabled-copier",
            ldap_bind_password_hash=hash_password("s3cret"),
        )
        session.add(printer)
        await session.commit()

    response = client.post(
        "/api/v1/internal/ldap/bind",
        headers=backend_headers,
        json={"bind_identifier": "disabled-copier", "password": "s3cret"},
    )
    assert response.json()["success"] is False


def test_bind_requires_backend_token(client, enabled_printer):
    response = client.post(
        "/api/v1/internal/ldap/bind",
        json={"bind_identifier": "front-office-copier", "password": "s3cret"},
    )
    assert response.status_code == 401


# --- search ---


def test_search_empty_when_relay_disabled(client, backend_headers):
    response = client.post(
        "/api/v1/internal/ldap/search",
        headers=backend_headers,
        json={
            "filter_attr": "mail",
            "filter_type": "equality",
            "filter_value": "matt.anderson@example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["entries"] == []


def test_search_equality_by_mail(client, auth_headers, backend_headers):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/search",
        headers=backend_headers,
        json={
            "filter_attr": "mail",
            "filter_type": "equality",
            "filter_value": "matt.anderson@example.com",
        },
    )
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["cn"] == "Matt Anderson"
    assert entries[0]["mail"] == "matt.anderson@example.com"
    assert entries[0]["employee_number"] == "1001"
    assert entries[0]["dn"] == "mail=matt.anderson@example.com,ou=people,dc=printops,dc=local"


def test_search_substring_by_cn(client, auth_headers, backend_headers):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/search",
        headers=backend_headers,
        json={"filter_attr": "cn", "filter_type": "substring", "filter_value": "smith"},
    )
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["mail"] == "jane.smith@example.com"


def test_search_no_match(client, auth_headers, backend_headers):
    _enable_relay(client, auth_headers)
    response = client.post(
        "/api/v1/internal/ldap/search",
        headers=backend_headers,
        json={"filter_attr": "cn", "filter_type": "substring", "filter_value": "nobody-like-this"},
    )
    assert response.json()["entries"] == []


def test_search_uses_configured_base_dn(client, auth_headers, backend_headers):
    update = client.put(
        "/api/v1/settings/ldap",
        headers=auth_headers,
        json={"enabled": True, "base_dn": "dc=example,dc=com"},
    )
    assert update.status_code == 200
    response = client.post(
        "/api/v1/internal/ldap/search",
        headers=backend_headers,
        json={
            "filter_attr": "mail",
            "filter_type": "equality",
            "filter_value": "matt.anderson@example.com",
        },
    )
    entries = response.json()["entries"]
    assert entries[0]["dn"] == "mail=matt.anderson@example.com,ou=people,dc=example,dc=com"


def test_search_requires_backend_token(client):
    response = client.post(
        "/api/v1/internal/ldap/search",
        json={"filter_attr": "mail", "filter_type": "equality", "filter_value": "x"},
    )
    assert response.status_code == 401


# --- settings ---


def test_ldap_settings_default_disabled(client, auth_headers):
    response = client.get("/api/v1/settings/ldap", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["base_dn"] == "dc=printops,dc=local"
    assert body["port"] == 389


def test_ldap_settings_update_requires_admin(client):
    response = client.put("/api/v1/settings/ldap", json={"enabled": True})
    assert response.status_code == 401


def test_internal_ldap_settings_for_relay_service(client, backend_headers):
    response = client.get("/api/v1/internal/ldap/settings", headers=backend_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["base_dn"] == "dc=printops,dc=local"
    assert body["port"] == 389


def test_internal_ldap_settings_requires_backend_token(client):
    response = client.get("/api/v1/internal/ldap/settings")
    assert response.status_code == 401
