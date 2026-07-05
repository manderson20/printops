import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base


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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_connector_types_only_lists_registered_connectors(client, auth_headers):
    response = client.get("/api/v1/mfp-devices/connector-types", headers=auth_headers)
    assert response.status_code == 200
    assert {c["connector_type"] for c in response.json()} == {
        "generic_csv",
        "generic_snmp",
        "canon_department_id",
        "konica_bizhub",
    }


def test_create_rejects_unknown_connector_type(client, auth_headers):
    response = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "X", "connector_type": "lexmark_accounting"},
    )
    assert response.status_code == 422


def test_create_list_get_delete(client, auth_headers):
    create = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Copy Room MFP", "vendor": "canon", "connector_type": "generic_csv"},
    )
    assert create.status_code == 201, create.text
    body = create.json()
    device_id = body["id"]
    # Every capability starts unassessed (None), not a false "unsupported".
    assert all(v is None for v in body["capabilities"].values())
    assert body["capabilities_source"] is None

    listing = client.get("/api/v1/mfp-devices", headers=auth_headers)
    assert listing.status_code == 200 and len(listing.json()) == 1

    get_one = client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert get_one.status_code == 200

    deleted = client.delete(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert missing.status_code == 404


def test_update_capabilities_manually(client, auth_headers):
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Front Office MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    updated = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"capabilities": {"walkup_copy_accounting": True, "badge_card_auth": False}},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["capabilities"]["walkup_copy_accounting"] is True
    assert body["capabilities"]["badge_card_auth"] is False
    # Untouched capabilities stay unassessed, not silently flipped to False.
    assert body["capabilities"]["user_code_pin_auth"] is None
    assert body["capabilities_source"] == "manual"


def test_test_connection_honest_about_unsupported_connector(client, auth_headers):
    """generic_csv has no live-connection concept — never fakes support."""
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "CSV-only MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    response = client.post(f"/api/v1/mfp-devices/{device_id}/test-connection", headers=auth_headers)
    assert response.status_code == 400
    assert "doesn't support" in response.json()["detail"] or "can't test" in response.json()["detail"]


def test_check_meter_rejects_non_snmp_connector(client, auth_headers):
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "CSV-only MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    response = client.post(f"/api/v1/mfp-devices/{device_id}/check-meter", headers=auth_headers)
    assert response.status_code == 400
