import uuid
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
from app.models.job import Job
from app.models.mosyle import MosyleDevice
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
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(name="Test Printer", ip_address="10.0.0.9")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


@pytest_asyncio.fixture
async def seeded_mosyle_device(db_session_factory):
    async with db_session_factory() as session:
        session.add(
            MosyleDevice(
                mac_address="AA:BB:CC:DD:EE:FF",
                serial_number="C02ABC123",
                device_name="Matt's MacBook",
                user_email=None,
                user_name="matt",
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def seeded_roster(db_session_factory):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email="matt.anderson@example.com", name="Matt Anderson", synced_at=datetime.now(UTC)
            )
        )
        session.add(
            GoogleWorkspaceUser(
                email="matt.jones@example.com", name="Matt Jones", synced_at=datetime.now(UTC)
            )
        )
        await session.commit()


def test_list_devices_requires_auth(client, seeded_mosyle_device):
    response = client.get("/api/v1/devices")
    assert response.status_code == 401


def test_list_devices_merges_mosyle_cache(client, seeded_mosyle_device, auth_headers):
    response = client.get("/api/v1/devices", headers=auth_headers)
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert devices[0]["source"] == "mosyle"
    assert devices[0]["reported_username"] == "matt"
    assert devices[0]["reported_email"] is None
    assert devices[0]["override_email"] is None


def test_set_override_rejects_email_not_in_roster(client, seeded_mosyle_device, auth_headers):
    response = client.put(
        "/api/v1/devices/AA:BB:CC:DD:EE:FF/override",
        json={"resolved_email": "not-a-real-user@example.com"},
        headers=auth_headers,
    )
    assert response.status_code == 400


@pytest_asyncio.fixture
async def two_jobs_one_matching_mac(db_session_factory, printer_id):
    # One job resolved from the overridden device's MAC, one from a
    # different MAC entirely — only the former should be touched by a
    # backfill.
    async with db_session_factory() as session:
        session.add(
            Job(
                printer_id=uuid.UUID(printer_id),
                submitted_by="matt",
                attribution_method="cups",
                mac_address="AA:BB:CC:DD:EE:FF",
            )
        )
        session.add(
            Job(
                printer_id=uuid.UUID(printer_id),
                submitted_by="matt",
                attribution_method="cups",
                mac_address="11:22:33:44:55:66",
            )
        )
        await session.commit()


def test_set_override_succeeds_and_backfills_matching_job_only(
    client, auth_headers, seeded_mosyle_device, seeded_roster, two_jobs_one_matching_mac
):
    response = client.put(
        "/api/v1/devices/AA:BB:CC:DD:EE:FF/override",
        json={"resolved_email": "Matt.Anderson@example.com", "note": "Matt A's MacBook"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved_email"] == "matt.anderson@example.com"
    assert body["backfilled_job_count"] == 1

    jobs = client.get("/api/v1/jobs", headers=auth_headers).json()
    overridden = [j for j in jobs if j["attribution_method"] == "override"]
    untouched = [j for j in jobs if j["attribution_method"] == "cups"]
    assert len(overridden) == 1
    assert overridden[0]["submitted_by"] == "matt.anderson@example.com"
    assert len(untouched) == 1
    assert untouched[0]["submitted_by"] == "matt"


def test_delete_nonexistent_override_404(client, auth_headers):
    response = client.delete("/api/v1/devices/AA:BB:CC:DD:EE:FF/override", headers=auth_headers)
    assert response.status_code == 404


def test_delete_override_removes_it(client, seeded_mosyle_device, seeded_roster, auth_headers):
    client.put(
        "/api/v1/devices/AA:BB:CC:DD:EE:FF/override",
        json={"resolved_email": "matt.anderson@example.com"},
        headers=auth_headers,
    )
    response = client.delete("/api/v1/devices/AA:BB:CC:DD:EE:FF/override", headers=auth_headers)
    assert response.status_code == 204

    devices = client.get("/api/v1/devices", headers=auth_headers).json()
    assert devices[0]["override_email"] is None


def test_list_google_workspace_users_roster(client, seeded_roster, auth_headers):
    response = client.get("/api/v1/settings/google-workspace/users", headers=auth_headers)
    assert response.status_code == 200
    emails = {u["email"] for u in response.json()}
    assert emails == {"matt.anderson@example.com", "matt.jones@example.com"}
