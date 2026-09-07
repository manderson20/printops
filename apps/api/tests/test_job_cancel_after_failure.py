"""A job PrintOps calls failed can still be sitting on the CUPS queue.

On 2026-08-24 the Graphic Arts Kyocera had a job that PrintOps had marked
`failed` at 23:10 and that cupsd still held at 23:40, retrying it and stopping
the printer's queue on every attempt. Cancel used to accept only `forwarding`,
so the one row an admin could see about the job that was taking the printer
down was the one row with no button on it.

`forwarded` stays uncancellable — those pages exist — and held jobs keep their
own Discard, which deletes a spooled document rather than a queued job.
"""

import uuid

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
from app.printers.job_control import CupsJobIdentity


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
async def job_factory(db_session_factory):
    async def make(
        status: str, cups_job_id: int | None = 5814, cups_job_uuid: str | None = None
    ) -> str:
        async with db_session_factory() as session:
            printer = Printer(id=uuid.uuid4(), name="LCACTC - GA Kyocera", ip_address="10.50.1.37")
            session.add(printer)
            job = Job(
                id=uuid.uuid4(),
                printer_id=printer.id,
                status=status,
                cups_job_id=cups_job_id,
                cups_job_uuid=cups_job_uuid,
                document_name="Advisory.pdf",
                submitted_by="hfiala",
            )
            session.add(job)
            await session.commit()
            return str(job.id)

    return make


@pytest.fixture
def cancelled_ids(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr("app.routers.jobs.cancel_cups_job", seen.append)
    return seen


@pytest.fixture
def cups_says(monkeypatch):
    """Stands in for cupsd's answer to "whose job is id N right now?".

    Defaults to "the same job this row describes", which is the ordinary case;
    tests that care about a reused id say so explicitly."""

    def set_identity(identity):
        monkeypatch.setattr(
            "app.routers.jobs.cups_job_identity", lambda printer_id, cups_job_id: identity
        )

    set_identity(CupsJobIdentity(uuid=None, owner="hfiala"))
    return set_identity


@pytest.mark.parametrize("status", ["forwarding", "failed", "cancelled", "received"])
async def test_anything_that_has_not_printed_can_be_cancelled(
    client, auth_headers, job_factory, cancelled_ids, cups_says, status
):
    job_id = await job_factory(status)
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    # The point of the endpoint: the job is gone from the print server, not
    # merely relabelled in ours.
    assert cancelled_ids == [5814]


async def test_a_printed_job_cannot_be_cancelled(client, auth_headers, job_factory, cancelled_ids):
    job_id = await job_factory("forwarded")
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 400
    assert "already printed" in response.json()["detail"]
    assert cancelled_ids == []


async def test_a_held_job_is_discarded_not_cancelled(
    client, auth_headers, job_factory, cancelled_ids
):
    job_id = await job_factory("held")
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 400
    assert "discarded" in response.json()["detail"]
    assert cancelled_ids == []


async def test_a_job_with_no_cups_id_says_so(client, auth_headers, job_factory, cancelled_ids):
    job_id = await job_factory("failed", cups_job_id=None)
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 400
    assert cancelled_ids == []


async def test_a_reused_job_id_is_not_cancelled_out_from_under_someone(
    client, auth_headers, job_factory, cancelled_ids, cups_says
):
    """CUPS job ids restart from 1 when the spool is cleared, so an old row can
    name an id that now belongs to a stranger's document. Closing our row must
    not cancel theirs."""
    job_id = await job_factory("failed")
    cups_says(CupsJobIdentity(uuid=None, owner="someone.else@example.org"))
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert cancelled_ids == []


async def test_a_job_uuid_settles_it_when_the_owner_would_not(
    client, auth_headers, job_factory, cancelled_ids, cups_says
):
    job_id = await job_factory("failed", cups_job_uuid="urn:uuid:the-original")
    cups_says(CupsJobIdentity(uuid="urn:uuid:something-else", owner="hfiala"))
    client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled_ids == []

    job_id = await job_factory("failed", cups_job_uuid="urn:uuid:the-original")
    cups_says(CupsJobIdentity(uuid="urn:uuid:the-original", owner="anybody"))
    client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled_ids == [5814]


async def test_a_job_cupsd_has_never_heard_of_just_closes(
    client, auth_headers, job_factory, cancelled_ids, cups_says
):
    job_id = await job_factory("failed")
    cups_says(None)
    response = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert cancelled_ids == []
