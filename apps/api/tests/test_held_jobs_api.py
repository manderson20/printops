from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError


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


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(name="Color Printer", ip_address="10.0.0.9")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer.id


async def _make_job(db_session_factory, printer_id, submitted_by, **overrides):
    fields = {
        "printer_id": printer_id,
        "submitted_by": submitted_by,
        "status": "held",
        "hold_reason": "quota",
        "held_file_path": "/var/spool/printops-held/x",
        "held_job_options": "sides=one-sided",
        "document_name": "Report.pdf",
        **overrides,
    }
    async with db_session_factory() as session:
        job = Job(**fields)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


def test_requires_auth(client, printer_id):
    response = client.get("/api/v1/quota-holds")
    assert response.status_code == 401


async def test_lists_only_quota_held_jobs(client, auth_headers, printer_id, db_session_factory):
    await _make_job(db_session_factory, printer_id, "matt@example.org")
    await _make_job(db_session_factory, printer_id, "other@example.org", hold_reason="pin_release")
    await _make_job(db_session_factory, printer_id, "third@example.org", status="forwarded")

    response = client.get("/api/v1/quota-holds", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["submitted_by"] == "matt@example.org"
    assert body[0]["printer_name"] == "Color Printer"


async def test_release_succeeds(client, auth_headers, printer_id, db_session_factory):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org")
    with patch("app.routers.quota_holds.submit_released_job", return_value="request id is x-1"):
        response = client.post(f"/api/v1/quota-holds/{job.id}/release", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "forwarded"


async def test_release_failure_marks_job_failed(
    client, auth_headers, printer_id, db_session_factory
):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org")
    with patch(
        "app.routers.quota_holds.submit_released_job", side_effect=ReleaseError("lp exploded")
    ):
        response = client.post(f"/api/v1/quota-holds/{job.id}/release", headers=auth_headers)
    assert response.status_code == 502


async def test_cannot_release_pin_release_job_via_admin_route(
    client, auth_headers, printer_id, db_session_factory
):
    job = await _make_job(
        db_session_factory, printer_id, "matt@example.org", hold_reason="pin_release"
    )
    response = client.post(f"/api/v1/quota-holds/{job.id}/release", headers=auth_headers)
    assert response.status_code == 404


async def test_cannot_release_already_forwarded_job(
    client, auth_headers, printer_id, db_session_factory
):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org", status="forwarded")
    response = client.post(f"/api/v1/quota-holds/{job.id}/release", headers=auth_headers)
    assert response.status_code == 404
