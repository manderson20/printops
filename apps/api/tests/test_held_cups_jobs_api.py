"""The admin side of app/printers/crash_guard.py: seeing, releasing and
cancelling a job PrintOps set aside because its printer went down twice while
receiving it.

Release and cancel both take a bare CUPS job id, and CUPS job ids are global
across every printer on the server. So the endpoints must act only on a job
actually held on the printer in the URL, and these tests hold them to that
with cupsd stubbed out.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.printers import crash_guard
from app.printers.job_control import HeldCupsJob, JobControlError
from app.routers import printers as printers_router

POISON = 12355
OTHER = 12400


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
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(id=uuid.uuid4(), name="MS Office Printer", ip_address="10.20.1.29")
        session.add(printer)
        await session.commit()
        return str(printer.id)


def _held(cups_job_id=POISON, queue="release"):
    return HeldCupsJob(
        cups_job_id=cups_job_id,
        queue=queue,
        document_name="8th Grade Missing Work",
        owner="a teacher",
        size_bytes=437248,
        submitted_at=datetime(2026, 9, 11, 18, 57, 50, tzinfo=UTC),
    )


class _Cupsd:
    def __init__(self, monkeypatch, held=None, fail=False):
        self.held = [_held()] if held is None else held
        self.released: list[int] = []
        self.cancelled: list[int] = []

        def _act(record):
            def _run(cups_job_id):
                if fail:
                    raise JobControlError("lp: Unable to hold job")
                record.append(cups_job_id)

            return _run

        monkeypatch.setattr(printers_router, "held_cups_jobs", lambda _pid: self.held)
        monkeypatch.setattr(printers_router, "release_cups_job", _act(self.released))
        monkeypatch.setattr(printers_router, "cancel_cups_job", _act(self.cancelled))


def _remember_holding(printer_id, cups_job_id=POISON):
    crash_guard.note_lost_during(printer_id, (cups_job_id,))
    crash_guard.note_lost_during(printer_id, (cups_job_id,))
    crash_guard.record_held(
        printer_id,
        crash_guard.HeldSuspect(
            cups_job_id=cups_job_id,
            document_name="8th Grade Missing Work",
            owner="a teacher",
            held_at=datetime(2026, 9, 14, 15, 0, tzinfo=UTC),
        ),
    )


async def _audit_actions(db_session_factory):
    async with db_session_factory() as session:
        return [row.action for row in (await session.execute(select(AuditEvent))).scalars()]


async def test_held_jobs_are_listed_with_who_held_them(
    client, auth_headers, printer_id, monkeypatch
):
    _Cupsd(monkeypatch, held=[_held(POISON), _held(OTHER, queue="client")])
    _remember_holding(printer_id, POISON)

    response = client.get(f"/api/v1/printers/{printer_id}/held-cups-jobs", headers=auth_headers)

    assert response.status_code == 200, response.text
    by_id = {job["cups_job_id"]: job for job in response.json()}
    assert by_id[POISON]["held_by_printops"] is True
    assert by_id[POISON]["held_at"] is not None
    assert by_id[POISON]["document_name"] == "8th Grade Missing Work"
    # A hold PrintOps did not place is still a held job, and still listed.
    assert by_id[OTHER]["held_by_printops"] is False


async def test_a_hold_cupsd_no_longer_has_is_forgotten(
    client, auth_headers, printer_id, monkeypatch
):
    """Released from the CUPS web interface, say. The printer must not go on
    saying a job is held."""
    _Cupsd(monkeypatch, held=[])
    _remember_holding(printer_id, POISON)

    response = client.get(f"/api/v1/printers/{printer_id}/held-cups-jobs", headers=auth_headers)

    assert response.json() == []
    assert crash_guard.held(printer_id) == []


async def test_cupsd_not_answering_is_not_an_empty_list(
    client, auth_headers, printer_id, monkeypatch
):
    monkeypatch.setattr(printers_router, "held_cups_jobs", lambda _pid: None)

    response = client.get(f"/api/v1/printers/{printer_id}/held-cups-jobs", headers=auth_headers)

    assert response.status_code == 502


async def test_releasing_sends_it_again_and_keeps_it_on_a_short_leash(
    client, auth_headers, printer_id, monkeypatch, db_session_factory
):
    cupsd = _Cupsd(monkeypatch)
    _remember_holding(printer_id, POISON)

    response = client.post(
        f"/api/v1/printers/{printer_id}/held-cups-jobs/{POISON}/release", headers=auth_headers
    )

    assert response.status_code == 204, response.text
    assert cupsd.released == [POISON]
    assert crash_guard.held(printer_id) == []
    # One more loss while it is being sent and it is held again.
    assert crash_guard.note_lost_during(printer_id, (POISON,)) == [POISON]
    assert "printer.release_held_job" in await _audit_actions(db_session_factory)


async def test_a_job_not_held_on_this_printer_is_refused(
    client, auth_headers, printer_id, monkeypatch
):
    """CUPS job ids are global. A release aimed at the wrong printer — or at a
    job that is printing — must not reach lp."""
    cupsd = _Cupsd(monkeypatch, held=[_held(OTHER)])

    for action in ("release", "cancel"):
        response = client.post(
            f"/api/v1/printers/{printer_id}/held-cups-jobs/{POISON}/{action}",
            headers=auth_headers,
        )
        assert response.status_code == 404

    assert cupsd.released == []
    assert cupsd.cancelled == []


async def test_a_release_cupsd_refuses_is_reported_and_the_hold_remembered(
    client, auth_headers, printer_id, monkeypatch
):
    _Cupsd(monkeypatch, fail=True)
    _remember_holding(printer_id, POISON)

    response = client.post(
        f"/api/v1/printers/{printer_id}/held-cups-jobs/{POISON}/release", headers=auth_headers
    )

    assert response.status_code == 502
    assert [s.cups_job_id for s in crash_guard.held(printer_id)] == [POISON]


async def test_cancelling_closes_the_job_row_too(
    client, auth_headers, printer_id, monkeypatch, db_session_factory
):
    """A job on the client-facing queue still has a row reading failed. Left
    alone it would say so for ever, about a job nobody will print."""
    cupsd = _Cupsd(monkeypatch, held=[_held(POISON, queue="client")])
    _remember_holding(printer_id, POISON)
    async with db_session_factory() as session:
        job = Job(
            id=uuid.uuid4(),
            printer_id=uuid.UUID(printer_id),
            cups_job_id=POISON,
            status="failed",
            document_name="8th Grade Missing Work",
            submitted_by="a teacher",
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    response = client.post(
        f"/api/v1/printers/{printer_id}/held-cups-jobs/{POISON}/cancel", headers=auth_headers
    )

    assert response.status_code == 204, response.text
    assert cupsd.cancelled == [POISON]
    assert crash_guard.held(printer_id) == []
    assert crash_guard.strikes(printer_id, POISON) == 0
    async with db_session_factory() as session:
        row = await session.get(Job, job_id)
        assert row.status == "cancelled"
        assert "while held" in row.error_message
    assert "printer.cancel_held_job" in await _audit_actions(db_session_factory)
