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
                email="manderson@brookfieldr3.org",
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
        printer = Printer(name="Test Printer", ip_address="10.0.0.9")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


def _make_job(client, printer_id, backend_headers, submitted_by):
    create = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id, "submitted_by": submitted_by}, headers=backend_headers
    )
    return create.json()["id"]


def test_create_rejects_email_not_in_roster(client, auth_headers):
    response = client.post(
        "/api/v1/attribution-aliases",
        headers=auth_headers,
        json={"alias": "matt", "resolved_email": "nobody@brookfieldr3.org"},
    )
    assert response.status_code == 400


def test_create_backfills_existing_jobs(client, auth_headers, printer_id, backend_headers):
    _make_job(client, printer_id, backend_headers, "matt")
    _make_job(client, printer_id, backend_headers, "matt")
    _make_job(client, printer_id, backend_headers, "someone.else@brookfieldr3.org")

    response = client.post(
        "/api/v1/attribution-aliases",
        headers=auth_headers,
        json={"alias": "matt", "resolved_email": "manderson@brookfieldr3.org"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["backfilled_job_count"] == 2
    assert body["source"] == "manual"

    jobs = client.get("/api/v1/jobs", headers=auth_headers).json()
    matt_jobs = [j for j in jobs if j["submitted_by"] == "manderson@brookfieldr3.org"]
    assert len(matt_jobs) == 2
    assert all(j["attribution_method"] == "alias" for j in matt_jobs)


def test_create_rejects_duplicate_alias(client, auth_headers):
    payload = {"alias": "matt", "resolved_email": "manderson@brookfieldr3.org"}
    first = client.post("/api/v1/attribution-aliases", headers=auth_headers, json=payload)
    assert first.status_code == 201
    second = client.post("/api/v1/attribution-aliases", headers=auth_headers, json=payload)
    assert second.status_code == 409


def test_alias_matching_is_case_insensitive(client, auth_headers):
    response = client.post(
        "/api/v1/attribution-aliases",
        headers=auth_headers,
        json={"alias": "Matt", "resolved_email": "MAndersoN@brookfieldr3.org"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["alias"] == "matt"
    assert body["resolved_email"] == "manderson@brookfieldr3.org"


def test_list_and_delete(client, auth_headers):
    alias_id = client.post(
        "/api/v1/attribution-aliases",
        headers=auth_headers,
        json={"alias": "matt", "resolved_email": "manderson@brookfieldr3.org"},
    ).json()["id"]

    listing = client.get("/api/v1/attribution-aliases", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1

    deleted = client.delete(f"/api/v1/attribution-aliases/{alias_id}", headers=auth_headers)
    assert deleted.status_code == 204

    listing_after = client.get("/api/v1/attribution-aliases", headers=auth_headers).json()
    assert listing_after["items"] == []
    assert listing_after["total"] == 0


def test_list_attribution_aliases_pagination_and_search(client, auth_headers):
    client.post(
        "/api/v1/attribution-aliases",
        headers=auth_headers,
        json={"alias": "matt", "resolved_email": "manderson@brookfieldr3.org"},
    )

    response = client.get(
        "/api/v1/attribution-aliases", headers=auth_headers, params={"page": 1, "page_size": 1}
    )
    body = response.json()
    assert body["page_size"] == 1
    assert len(body["items"]) == 1

    response = client.get(
        "/api/v1/attribution-aliases", headers=auth_headers, params={"search": "matt"}
    )
    assert response.json()["total"] == 1

    response = client.get(
        "/api/v1/attribution-aliases", headers=auth_headers, params={"search": "no-such-alias"}
    )
    assert response.json()["total"] == 0
