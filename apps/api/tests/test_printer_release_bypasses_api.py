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
                email="manderson@example.org",
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
        printer = Printer(name="Copier Room", ip_address="10.0.0.9", release_required=True)
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


def test_create_rejects_email_not_in_roster(client, auth_headers, printer_id):
    response = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        headers=auth_headers,
        json={"user_email": "nobody@example.org"},
    )
    assert response.status_code == 400


def test_create_and_list(client, auth_headers, printer_id):
    response = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        headers=auth_headers,
        json={"user_email": "MAnderson@Example.org"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_email"] == "manderson@example.org"

    listing = client.get(f"/api/v1/printers/{printer_id}/release-bypasses", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_create_duplicate_conflicts(client, auth_headers, printer_id):
    payload = {"user_email": "manderson@example.org"}
    first = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses", headers=auth_headers, json=payload
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses", headers=auth_headers, json=payload
    )
    assert second.status_code == 409


def test_delete(client, auth_headers, printer_id):
    create = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        headers=auth_headers,
        json={"user_email": "manderson@example.org"},
    )
    bypass_id = create.json()["id"]

    deleted = client.delete(
        f"/api/v1/printers/{printer_id}/release-bypasses/{bypass_id}", headers=auth_headers
    )
    assert deleted.status_code == 204

    listing = client.get(f"/api/v1/printers/{printer_id}/release-bypasses", headers=auth_headers)
    assert listing.json() == []


def test_viewer_cannot_write(client, printer_id):
    response = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        json={"user_email": "manderson@example.org"},
    )
    assert response.status_code == 401


def test_bypassed_user_job_is_not_held(client, auth_headers, backend_headers, printer_id):
    """The actual point of the feature: a bypassed user's job at a
    release_required printer isn't held, while everyone else's still is."""
    client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        headers=auth_headers,
        json={"user_email": "manderson@example.org"},
    )

    bypassed_job = client.post(
        "/api/v1/jobs",
        headers=backend_headers,
        json={"printer_id": printer_id, "submitted_by": "manderson@example.org"},
    )
    # create_job always sets status="forwarding" at creation time regardless
    # of hold_reason — the CUPS backend script (infra/cups/backends/printops)
    # is what later PATCHes to status="held" once it reads hold_reason, so
    # hold_reason (decided by resolve_hold_reason, this feature's actual
    # scope) is what these assertions check, not status.
    assert bypassed_job.status_code == 201
    assert bypassed_job.json()["hold_reason"] is None

    other_job = client.post(
        "/api/v1/jobs",
        headers=backend_headers,
        json={"printer_id": printer_id, "submitted_by": "someoneelse@example.org"},
    )
    assert other_job.status_code == 201
    assert other_job.json()["hold_reason"] == "pin_release"
