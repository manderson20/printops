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
    response = client.get("/api/v1/held-jobs")
    assert response.status_code == 401


async def test_lists_every_held_job_whatever_is_holding_it(
    client, auth_headers, printer_id, db_session_factory
):
    """This used to list quota holds only, which left the release holds with
    no admin surface at all — see the module docstring on held_jobs.py for who
    that stranded."""
    await _make_job(db_session_factory, printer_id, "matt@example.org")
    await _make_job(db_session_factory, printer_id, "other@example.org", hold_reason="pin_release")
    await _make_job(db_session_factory, printer_id, "third@example.org", status="forwarded")

    response = client.get("/api/v1/held-jobs", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert {row["submitted_by"] for row in body} == {"matt@example.org", "other@example.org"}
    assert {row["hold_reason"] for row in body} == {"quota", "pin_release"}
    assert body[0]["printer_name"] == "Color Printer"


async def test_release_succeeds(client, auth_headers, printer_id, db_session_factory):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org")
    with patch("app.routers.held_jobs.submit_released_job", return_value="request id is x-1"):
        response = client.post(f"/api/v1/held-jobs/{job.id}/release", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "forwarded"


async def test_release_failure_marks_job_failed(
    client, auth_headers, printer_id, db_session_factory
):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org")
    with patch(
        "app.routers.held_jobs.submit_released_job", side_effect=ReleaseError("lp exploded")
    ):
        response = client.post(f"/api/v1/held-jobs/{job.id}/release", headers=auth_headers)
    assert response.status_code == 502


async def test_an_admin_can_release_a_pin_release_hold(
    client, auth_headers, printer_id, db_session_factory
):
    """The case that motivated this: a test page is submitted through the
    printer's own queue on purpose, so a release-enabled printer holds it like
    any other job. The kiosk can only hand it back to the person whose PIN maps
    to the address on the job, so anyone the roster doesn't know, or who has no
    PIN yet, previously had no way to release it at all."""
    job = await _make_job(
        db_session_factory, printer_id, "matt@example.org", hold_reason="pin_release"
    )
    with patch("app.routers.held_jobs.submit_released_job", return_value="request id is x-1"):
        response = client.post(f"/api/v1/held-jobs/{job.id}/release", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "forwarded"


async def test_a_follow_me_job_will_not_be_released_without_a_printer(
    client, auth_headers, db_session_factory
):
    """A Follow-Me job was addressed to a virtual queue with no device behind
    it. The kiosk resolves that to whichever printer the person is standing at;
    an admin isn't standing anywhere, so they have to say which."""
    async with db_session_factory() as session:
        virtual = Printer(name="Follow-Me", ip_address="", is_virtual=True)
        session.add(virtual)
        await session.commit()
        await session.refresh(virtual)
        virtual_id = virtual.id
    job = await _make_job(
        db_session_factory, virtual_id, "matt@example.org", hold_reason="follow_me"
    )

    response = client.post(f"/api/v1/held-jobs/{job.id}/release", headers=auth_headers)

    assert response.status_code == 400
    assert "Choose the printer" in response.json()["detail"]


async def test_a_follow_me_job_is_released_at_the_printer_the_admin_names(
    client, auth_headers, printer_id, db_session_factory
):
    async with db_session_factory() as session:
        virtual = Printer(name="Follow-Me", ip_address="", is_virtual=True)
        session.add(virtual)
        await session.commit()
        await session.refresh(virtual)
        virtual_id = virtual.id
    job = await _make_job(
        db_session_factory, virtual_id, "matt@example.org", hold_reason="follow_me"
    )

    with patch(
        "app.routers.held_jobs.submit_released_job", return_value="request id is x-1"
    ) as submit:
        response = client.post(
            f"/api/v1/held-jobs/{job.id}/release",
            headers=auth_headers,
            json={"printer_id": str(printer_id)},
        )

    assert response.status_code == 200, response.text
    assert submit.call_args.args[0] == str(printer_id)


async def test_cannot_release_already_forwarded_job(
    client, auth_headers, printer_id, db_session_factory
):
    job = await _make_job(db_session_factory, printer_id, "matt@example.org", status="forwarded")
    response = client.post(f"/api/v1/held-jobs/{job.id}/release", headers=auth_headers)
    assert response.status_code == 404
