"""Letting people throw away their own held jobs.

The person who knows a hold is pointless is usually the one who made it — they
sent the same thing three times, or sent it to the wrong printer. Until now
their only ways out were an admin discarding it or waiting out the expiry.

What this must not become is a way to reach anyone else's document, or a way to
learn what else is held. Ownership is enforced inside the same conditional
UPDATE that claims the row (app/held_jobs/service.py), and somebody else's job
answers exactly as a nonexistent one does.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer

PRINTER_ID = uuid.UUID("66b8aef6-f874-4bfe-ab54-b9b9147ae17e")
ME = "admin"
SOMEONE_ELSE = "casey.aide@example.org"


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
async def hold(db_session_factory, tmp_path):
    async def make(
        *, submitted_by: str = ME, status: str = "held", reason: str = "printer_offline"
    ):
        spool = tmp_path / f"held-{uuid.uuid4()}"
        spool.write_bytes(b"%PDF-1.4 someone's actual document")
        async with db_session_factory() as session:
            job = Job(
                id=uuid.uuid4(),
                printer_id=PRINTER_ID,
                status=status,
                hold_reason=reason,
                submitted_by=submitted_by,
                document_name="Duplicate.pdf",
                held_file_path=str(spool),
            )
            session.add(job)
            await session.commit()
            return str(job.id), spool

    return make


async def _job(db_session_factory, job_id):
    async with db_session_factory() as session:
        return (await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()


async def test_discarding_my_own_hold_destroys_the_document_but_keeps_the_row(
    client, auth_headers, hold, db_session_factory
):
    job_id, spool = await hold()
    response = client.post(f"/api/v1/print-queue/held/{job_id}/discard", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert not spool.exists()
    job = await _job(db_session_factory, job_id)
    # The record survives: reports and history count this job, and a discarded
    # hold has to mean one thing whoever discarded it.
    assert job.status == "cancelled"
    assert job.error_message == "Discarded without printing"
    assert job.held_file_path is None


async def test_it_disappears_from_the_queue_page_afterwards(client, auth_headers, hold):
    job_id, _ = await hold()
    assert len(client.get("/api/v1/print-queue", headers=auth_headers).json()["held"]) == 1
    client.post(f"/api/v1/print-queue/held/{job_id}/discard", headers=auth_headers)
    assert client.get("/api/v1/print-queue", headers=auth_headers).json()["held"] == []


async def test_i_cannot_discard_someone_elses_hold(client, auth_headers, hold, db_session_factory):
    job_id, spool = await hold(submitted_by=SOMEONE_ELSE)
    response = client.post(f"/api/v1/print-queue/held/{job_id}/discard", headers=auth_headers)
    assert response.status_code == 404
    # Same answer a nonexistent job gets: discarding at guessed ids must not
    # reveal what else is held, or whose it is.
    assert "under your name" in response.json()["detail"]
    assert spool.exists()
    job = await _job(db_session_factory, job_id)
    assert job.status == "held"
    assert job.held_file_path == str(spool)


async def test_a_nonexistent_job_answers_the_same_way(client, auth_headers):
    response = client.post(f"/api/v1/print-queue/held/{uuid.uuid4()}/discard", headers=auth_headers)
    assert response.status_code == 404
    assert "under your name" in response.json()["detail"]


async def test_a_job_that_stopped_being_held_says_so(client, auth_headers, hold):
    # The race this guards: app/printers/offline_holds.py releases a held job
    # the moment its printer answers, in another session. Whoever moves the row
    # out of "held" first wins, and the loser must be told rather than silently
    # deleting a document that is already printing.
    job_id, spool = await hold(status="forwarded")
    response = client.post(f"/api/v1/print-queue/held/{job_id}/discard", headers=auth_headers)
    assert response.status_code == 409
    assert "no longer held" in response.json()["detail"]
    assert spool.exists()


@pytest.mark.parametrize("reason", ["printer_offline", "quota", "pin_release", "follow_me"])
async def test_any_of_my_own_holds_can_be_discarded(client, auth_headers, hold, reason):
    # Quota included: the quota hold exists to stop a job printing without an
    # admin, and this doesn't print it. Throwing your own over-quota job away
    # asks nothing of anyone and consumes no quota.
    job_id, spool = await hold(reason=reason)
    response = client.post(f"/api/v1/print-queue/held/{job_id}/discard", headers=auth_headers)
    assert response.status_code == 200
    assert not spool.exists()


async def test_discarding_needs_a_login(client, hold):
    job_id, spool = await hold()
    assert client.post(f"/api/v1/print-queue/held/{job_id}/discard").status_code == 401
    assert spool.exists()
