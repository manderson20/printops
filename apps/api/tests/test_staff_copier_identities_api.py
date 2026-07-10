from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
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
    async with session_factory() as seed:
        seed.add(
            GoogleWorkspaceUser(
                email="jane.smith@district.org",
                name="Jane Smith",
                employee_id="12345",
                synced_at=datetime.now(UTC),
            )
        )
        seed.add(
            GoogleWorkspaceUser(
                email="no.identity@district.org",
                name="No Identity",
                employee_id="99999",
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
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_create_rejects_email_not_in_roster(client, auth_headers):
    response = client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={
            "staff_email": "nobody@district.org",
            "identity_type": "staff_id",
            "identity_value": "12345",
        },
    )
    assert response.status_code == 400


def test_create_and_list_by_staff(client, auth_headers):
    client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={
            "staff_email": "jane.smith@district.org",
            "identity_type": "staff_id",
            "identity_value": "12345",
        },
    )
    client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={
            "staff_email": "jane.smith@district.org",
            "identity_type": "badge_id",
            "identity_value": "0008473629",
        },
    )
    response = client.get(
        "/api/v1/staff-copier-identities/by-staff/jane.smith@district.org", headers=auth_headers
    )
    assert response.status_code == 200 and len(response.json()) == 2


def test_duplicate_org_wide_identity_conflicts(client, auth_headers):
    payload = {
        "staff_email": "jane.smith@district.org",
        "identity_type": "staff_id",
        "identity_value": "12345",
    }
    first = client.post("/api/v1/staff-copier-identities", headers=auth_headers, json=payload)
    assert first.status_code == 201
    second = client.post("/api/v1/staff-copier-identities", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_missing_identity_list_excludes_staff_with_one(client, auth_headers):
    client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={
            "staff_email": "jane.smith@district.org",
            "identity_type": "staff_id",
            "identity_value": "12345",
        },
    )
    response = client.get("/api/v1/staff-copier-identities/missing", headers=auth_headers)
    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert emails == {"no.identity@district.org"}


def test_update_and_delete(client, auth_headers):
    identity_id = client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={
            "staff_email": "jane.smith@district.org",
            "identity_type": "staff_id",
            "identity_value": "12345",
        },
    ).json()["id"]

    updated = client.patch(
        f"/api/v1/staff-copier-identities/{identity_id}",
        headers=auth_headers,
        json={"note": "primary ID"},
    )
    assert updated.status_code == 200 and updated.json()["note"] == "primary ID"

    deleted = client.delete(f"/api/v1/staff-copier-identities/{identity_id}", headers=auth_headers)
    assert deleted.status_code == 204

    remaining = client.get(
        "/api/v1/staff-copier-identities/by-staff/jane.smith@district.org", headers=auth_headers
    ).json()
    assert remaining == []
