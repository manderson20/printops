"""Discarding a hold destroys the document, never the record.

Every foreign key into a job's history is the accounting: the reports count
these rows. A hold an admin gives up on must therefore leave the row behind
exactly as the expiry sweep in app/main.py does — the spool file goes, the job
stays, marked cancelled. This is the same distinction the copier toggle turns
on, and it is easy to get wrong in the obvious direction (a DELETE endpoint).
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


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
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
async def held_job(db_session_factory, tmp_path):
    spool = tmp_path / "held-document"
    spool.write_bytes(b"%PDF-1.4 someone's actual document")
    async with db_session_factory() as session:
        printer = Printer(id=uuid.uuid4(), name="ES 4th Grade Printer", ip_address="10.10.1.10")
        session.add(printer)
        job = Job(
            id=uuid.uuid4(),
            printer_id=printer.id,
            status="held",
            hold_reason="pin_release",
            document_name="Field Trip Permission Slip.pdf",
            submitted_by="sayers",
            page_count=3,
            held_file_path=str(spool),
        )
        session.add(job)
        await session.commit()
        return str(job.id), spool


async def test_discard_deletes_the_document_but_keeps_the_job(
    client, auth_headers, held_job, db_session_factory
):
    job_id, spool = held_job
    assert spool.exists()

    response = client.post(f"/api/v1/held-jobs/{job_id}/discard", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert not spool.exists(), "the spool file must be gone"

    async with db_session_factory() as session:
        job = (await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
        assert job.status == "cancelled"
        assert job.error_message == "Discarded without printing"
        assert job.held_file_path is None
        assert job.page_count == 3, "history the reports count must survive"
        assert job.document_name == "Field Trip Permission Slip.pdf"


async def test_discard_refuses_a_job_that_is_not_held(
    client, auth_headers, held_job, db_session_factory
):
    """Only a hold can be discarded — a forwarded job has no document left to
    destroy, and letting this touch one would rewrite finished history."""
    job_id, _ = held_job
    async with db_session_factory() as session:
        job = (await session.execute(select(Job).where(Job.id == uuid.UUID(job_id)))).scalar_one()
        job.status = "forwarded"
        await session.commit()

    response = client.post(f"/api/v1/held-jobs/{job_id}/discard", headers=auth_headers)
    assert response.status_code == 404


async def test_discard_requires_an_admin(client, held_job):
    job_id, spool = held_job
    response = client.post(f"/api/v1/held-jobs/{job_id}/discard")
    assert response.status_code in (401, 403)
    assert spool.exists(), "an unauthenticated call must not destroy the document"
