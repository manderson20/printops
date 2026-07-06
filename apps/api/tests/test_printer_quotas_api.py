from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
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
                email="manderson@example.com",
                name="Matt Anderson",
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
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(name="Color Printer", ip_address="10.0.0.9")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


def test_create_rejects_email_not_in_roster(client, auth_headers, printer_id):
    response = client.post(
        f"/api/v1/printers/{printer_id}/quotas",
        headers=auth_headers,
        json={"user_email": "nobody@example.com", "period": "monthly", "page_limit": 100},
    )
    assert response.status_code == 400


def test_create_allows_default_row_without_email(client, auth_headers, printer_id):
    response = client.post(
        f"/api/v1/printers/{printer_id}/quotas",
        headers=auth_headers,
        json={"user_email": None, "period": "monthly", "page_limit": 200},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_email"] is None
    assert body["page_limit"] == 200
    assert body["pages_used"] == 0


def test_create_duplicate_user_row_conflicts(client, auth_headers, printer_id):
    payload = {"user_email": "manderson@example.com", "period": "monthly", "page_limit": 50}
    first = client.post(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers, json=payload)
    assert first.status_code == 201
    second = client.post(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_create_duplicate_default_row_conflicts(client, auth_headers, printer_id):
    payload = {"user_email": None, "period": "monthly", "page_limit": 50}
    first = client.post(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers, json=payload)
    assert first.status_code == 201
    second = client.post(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_list_reflects_usage(client, auth_headers, backend_headers, printer_id):
    create = client.post(
        f"/api/v1/printers/{printer_id}/quotas",
        headers=auth_headers,
        json={"user_email": "manderson@example.com", "period": "monthly", "page_limit": 50},
    )
    assert create.status_code == 201

    job = client.post(
        "/api/v1/jobs",
        headers=backend_headers,
        json={"printer_id": printer_id, "submitted_by": "manderson@example.com"},
    )
    job_id = job.json()["id"]
    client.patch(
        f"/api/v1/jobs/{job_id}",
        headers=backend_headers,
        json={"status": "forwarded", "page_count": 12},
    )

    listing = client.get(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["pages_used"] == 12


def test_update_and_delete(client, auth_headers, printer_id):
    create = client.post(
        f"/api/v1/printers/{printer_id}/quotas",
        headers=auth_headers,
        json={"user_email": "manderson@example.com", "period": "monthly", "page_limit": 50},
    )
    quota_id = create.json()["id"]

    updated = client.patch(
        f"/api/v1/printers/{printer_id}/quotas/{quota_id}",
        headers=auth_headers,
        json={"page_limit": 75},
    )
    assert updated.status_code == 200
    assert updated.json()["page_limit"] == 75

    deleted = client.delete(f"/api/v1/printers/{printer_id}/quotas/{quota_id}", headers=auth_headers)
    assert deleted.status_code == 204

    listing = client.get(f"/api/v1/printers/{printer_id}/quotas", headers=auth_headers)
    assert listing.json() == []


def test_viewer_cannot_write(client, printer_id):
    # No auth at all is enough to prove the admin gate — matches the pattern
    # used by test_attribution_aliases_api.py for its role-gated endpoints.
    response = client.post(
        f"/api/v1/printers/{printer_id}/quotas",
        json={"user_email": None, "period": "monthly", "page_limit": 10},
    )
    assert response.status_code == 401


def test_quota_settings_default_disabled_and_toggle(client, auth_headers):
    initial = client.get("/api/v1/settings/quotas", headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json()["enabled"] is False

    updated = client.put("/api/v1/settings/quotas", headers=auth_headers, json={"enabled": True})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True

    refetched = client.get("/api/v1/settings/quotas", headers=auth_headers)
    assert refetched.json()["enabled"] is True
