"""Held jobs on the queue page.

A job PrintOps holds — over quota, waiting for a PIN at the printer, or waiting
for a printer that is switched off — never enters a CUPS queue at all. Until
now that meant the person who sent it had no way to tell a job that is waiting
from a job that vanished: it is absent from the queue side of this page by
construction, and staff have no access to the admin Jobs page.

Only ever this person's own. A held job occupies nobody else's place in any
line, so listing other people's would be exposure with nothing to decide.
"""

import uuid
from datetime import UTC, datetime, timedelta

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

PRINTER_ID = uuid.UUID("66b8aef6-f874-4bfe-ab54-b9b9147ae17e")
ME = "admin"
SOMEONE_ELSE = "agerukink@brookfieldr3.org"


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
    async with session_factory() as session:
        session.add(Printer(id=PRINTER_ID, name="ES-MS Nurse Copier", ip_address="10.10.3.5"))
        await session.commit()
    yield session_factory
    await engine.dispose()


@pytest.fixture
def client(db_session_factory, monkeypatch):
    # Nothing is queued in cupsd in any of these — held jobs are the whole
    # point, and they live in the database, not on a queue.
    monkeypatch.setattr("app.routers.print_queue.queued_jobs", list)

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": ME, "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture
async def hold(db_session_factory):
    async def make(
        *,
        reason: str = "printer_offline",
        submitted_by: str = ME,
        expires_at: datetime | None = None,
        status: str = "held",
        document_name: str = "Choir Concert Programme.pdf",
    ) -> str:
        async with db_session_factory() as session:
            job = Job(
                id=uuid.uuid4(),
                printer_id=PRINTER_ID,
                status=status,
                hold_reason=reason,
                submitted_by=submitted_by,
                document_name=document_name,
                file_size_bytes=204800,
                held_file_path="/var/spool/printops-held/x",
                held_expires_at=expires_at,
            )
            session.add(job)
            await session.commit()
            return str(job.id)

    return make


async def test_my_held_job_is_listed_with_why(client, auth_headers, hold):
    await hold(reason="printer_offline")
    body = client.get("/api/v1/print-queue", headers=auth_headers).json()
    assert body["queues"] == []
    [held] = body["held"]
    assert held["document_name"] == "Choir Concert Programme.pdf"
    assert held["printer_name"] == "ES-MS Nurse Copier"
    assert held["reason"] == "printer_offline"
    assert held["size_bytes"] == 204800


@pytest.mark.parametrize("reason", ["printer_offline", "quota", "pin_release", "follow_me"])
async def test_every_kind_of_hold_shows_up(client, auth_headers, hold, reason):
    await hold(reason=reason)
    [held] = client.get("/api/v1/print-queue", headers=auth_headers).json()["held"]
    assert held["reason"] == reason


async def test_someone_elses_held_job_is_not_listed(client, auth_headers, hold):
    await hold(submitted_by=SOMEONE_ELSE)
    assert client.get("/api/v1/print-queue", headers=auth_headers).json()["held"] == []


async def test_a_job_that_is_no_longer_held_is_not_listed(client, auth_headers, hold):
    await hold(status="forwarded")
    await hold(status="cancelled")
    assert client.get("/api/v1/print-queue", headers=auth_headers).json()["held"] == []


async def test_an_expired_hold_is_not_listed(client, auth_headers, hold):
    # The sweep deletes the document shortly after this passes. Listing the job
    # as waiting when its document is already gone is worse than not listing it.
    await hold(expires_at=datetime.now(UTC) - timedelta(minutes=5))
    assert client.get("/api/v1/print-queue", headers=auth_headers).json()["held"] == []


async def test_a_hold_with_time_left_is_listed_with_its_deadline(client, auth_headers, hold):
    expires = datetime.now(UTC) + timedelta(hours=20)
    await hold(expires_at=expires)
    [held] = client.get("/api/v1/print-queue", headers=auth_headers).json()["held"]
    assert held["expires_at"] is not None


async def test_holds_need_a_login(client, hold):
    await hold()
    assert client.get("/api/v1/print-queue").status_code == 401
