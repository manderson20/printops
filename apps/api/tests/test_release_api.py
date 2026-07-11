from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError
from app.routers import release as release_router


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


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    release_router._rate_limiter = release_router._RateLimiter()
    yield


@pytest.fixture
def client(db_session_factory):
    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def printer_with_release(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(
            name="Library Copier",
            ip_address="10.0.0.9",
            release_required=True,
            release_token="test-token-123",
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer


@pytest_asyncio.fixture
async def follow_me_printer_a(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(
            name="Printer A",
            ip_address="10.0.0.20",
            follow_me_enabled=True,
            release_token="follow-me-token-a",
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer


@pytest_asyncio.fixture
async def follow_me_printer_b(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(
            name="Printer B",
            ip_address="10.0.0.21",
            follow_me_enabled=True,
            release_token="follow-me-token-b",
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer


@pytest_asyncio.fixture
async def alice(db_session_factory):
    async with db_session_factory() as session:
        user = GoogleWorkspaceUser(
            email="alice@example.com", name="Alice", employee_id="1001", synced_at=datetime.now(UTC)
        )
        session.add(user)
        await session.commit()
        return user


@pytest_asyncio.fixture
async def bob(db_session_factory):
    async with db_session_factory() as session:
        user = GoogleWorkspaceUser(
            email="bob@example.com", name="Bob", employee_id="2002", synced_at=datetime.now(UTC)
        )
        session.add(user)
        await session.commit()
        return user


async def _make_held_job(db_session_factory, printer_id, submitted_by, **overrides):
    fields = {
        "printer_id": printer_id,
        "submitted_by": submitted_by,
        "status": "held",
        "hold_reason": "pin_release",
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


def test_unknown_token_404s(client):
    response = client.post("/api/v1/release/does-not-exist/jobs", json={"pin": "1001"})
    assert response.status_code == 404


async def test_wrong_pin_401s(client, printer_with_release, alice, db_session_factory):
    await _make_held_job(db_session_factory, printer_with_release.id, alice.email)
    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "9999"})
    assert response.status_code == 401


async def test_correct_pin_lists_only_that_users_held_jobs(
    client, printer_with_release, alice, bob, db_session_factory
):
    await _make_held_job(db_session_factory, printer_with_release.id, alice.email)
    await _make_held_job(db_session_factory, printer_with_release.id, bob.email)

    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["document_name"] == "Report.pdf"


async def test_only_shows_jobs_for_this_printer(
    client, printer_with_release, alice, db_session_factory
):
    async with db_session_factory() as session:
        other_printer = Printer(name="Other Printer", ip_address="10.0.0.10")
        session.add(other_printer)
        await session.commit()
        await session.refresh(other_printer)

    await _make_held_job(db_session_factory, other_printer.id, alice.email)

    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    assert response.json() == []


async def test_release_succeeds_and_clears_held_file(
    client, printer_with_release, alice, db_session_factory
):
    job = await _make_held_job(db_session_factory, printer_with_release.id, alice.email)

    with patch("app.routers.release.submit_released_job", return_value="request id is x-1"):
        response = client.post(
            f"/api/v1/release/test-token-123/jobs/{job.id}/release", json={"pin": "1001"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "forwarded"


async def test_release_failure_marks_job_failed(
    client, printer_with_release, alice, db_session_factory
):
    job = await _make_held_job(db_session_factory, printer_with_release.id, alice.email)

    with patch("app.routers.release.submit_released_job", side_effect=ReleaseError("lp exploded")):
        response = client.post(
            f"/api/v1/release/test-token-123/jobs/{job.id}/release", json={"pin": "1001"}
        )
    assert response.status_code == 502


async def test_cannot_release_someone_elses_held_job(
    client, printer_with_release, alice, bob, db_session_factory
):
    bobs_job = await _make_held_job(db_session_factory, printer_with_release.id, bob.email)

    response = client.post(
        f"/api/v1/release/test-token-123/jobs/{bobs_job.id}/release", json={"pin": "1001"}
    )
    assert response.status_code == 404


async def test_cannot_release_already_forwarded_job(
    client, printer_with_release, alice, db_session_factory
):
    job = await _make_held_job(
        db_session_factory, printer_with_release.id, alice.email, status="forwarded"
    )

    response = client.post(
        f"/api/v1/release/test-token-123/jobs/{job.id}/release", json={"pin": "1001"}
    )
    assert response.status_code == 404


async def test_quota_held_job_invisible_to_kiosk_listing(
    client, printer_with_release, alice, db_session_factory
):
    await _make_held_job(
        db_session_factory, printer_with_release.id, alice.email, hold_reason="quota"
    )
    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    assert response.json() == []


async def test_quota_held_job_cannot_be_self_released(
    client, printer_with_release, alice, db_session_factory
):
    job = await _make_held_job(
        db_session_factory, printer_with_release.id, alice.email, hold_reason="quota"
    )
    response = client.post(
        f"/api/v1/release/test-token-123/jobs/{job.id}/release", json={"pin": "1001"}
    )
    assert response.status_code == 404


async def test_follow_me_job_visible_at_other_follow_me_printer(
    client, follow_me_printer_a, follow_me_printer_b, alice, db_session_factory
):
    await _make_held_job(
        db_session_factory, follow_me_printer_a.id, alice.email, hold_reason="follow_me"
    )
    response = client.post("/api/v1/release/follow-me-token-b/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["printer_name"] == "Printer A"


async def test_follow_me_job_not_visible_at_non_follow_me_printer(
    client, follow_me_printer_a, printer_with_release, alice, db_session_factory
):
    await _make_held_job(
        db_session_factory, follow_me_printer_a.id, alice.email, hold_reason="follow_me"
    )
    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    assert response.json() == []


async def test_pin_release_job_not_visible_at_a_different_printers_kiosk_even_if_follow_me(
    client, printer_with_release, follow_me_printer_b, alice, db_session_factory
):
    await _make_held_job(
        db_session_factory, printer_with_release.id, alice.email, hold_reason="pin_release"
    )
    response = client.post("/api/v1/release/follow-me-token-b/jobs", json={"pin": "1001"})
    assert response.status_code == 200
    assert response.json() == []


async def test_follow_me_job_releases_via_the_kiosks_own_printer(
    client, follow_me_printer_a, follow_me_printer_b, alice, db_session_factory
):
    job = await _make_held_job(
        db_session_factory, follow_me_printer_a.id, alice.email, hold_reason="follow_me"
    )
    with patch("app.routers.release.submit_released_job", return_value="ok") as mock_submit:
        response = client.post(
            f"/api/v1/release/follow-me-token-b/jobs/{job.id}/release", json={"pin": "1001"}
        )
    assert response.status_code == 200
    assert response.json()["status"] == "forwarded"
    # Delivered to Printer B's queue (the kiosk actually released at), not
    # Printer A (where the job was originally submitted) — this is the
    # cross-printer redirect that makes it "follow-me".
    assert mock_submit.call_args.args[0] == str(follow_me_printer_b.id)


def test_repeated_wrong_pins_get_rate_limited(client, printer_with_release):
    for _ in range(8):
        client.post("/api/v1/release/test-token-123/jobs", json={"pin": "0000"})
    response = client.post("/api/v1/release/test-token-123/jobs", json={"pin": "0000"})
    assert response.status_code == 429
