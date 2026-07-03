import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from datetime import UTC, datetime

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.printer import Printer
from app.printers.ipp_client import PrinterProbeError
from app.routers import printers as printers_router


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


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(name="Test Printer", ip_address="10.0.0.9")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


@pytest.fixture
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_failed_probe(monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        raise PrinterProbeError("Could not reach an IPP printer: timed out")

    monkeypatch.setattr(printers_router, "probe_printer", fake_probe_printer)


def test_create_job_requires_backend_token(client, printer_id):
    response = client.post("/api/v1/jobs", json={"printer_id": printer_id})
    assert response.status_code == 401


def test_create_job_rejects_wrong_token(client, printer_id):
    response = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id},
        headers={"X-Backend-Token": "wrong"},
    )
    assert response.status_code == 401


def test_create_and_update_job(client, printer_id, backend_headers):
    create = client.post(
        "/api/v1/jobs",
        json={
            "printer_id": printer_id,
            "cups_job_id": 42,
            "submitted_by": "jdoe",
            "file_size_bytes": 12345,
        },
        headers=backend_headers,
    )
    assert create.status_code == 201
    body = create.json()
    assert body["status"] == "forwarding"
    assert body["cups_job_id"] == 42
    assert body["submitted_by"] == "jdoe"

    updated = client.patch(
        f"/api/v1/jobs/{body['id']}",
        json={"status": "forwarded"},
        headers=backend_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "forwarded"
    assert updated.json()["error_message"] is None


def test_update_job_failure_path(client, printer_id, backend_headers):
    create = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers
    )
    job_id = create.json()["id"]

    updated = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": "failed", "error_message": "ipp backend exited 1"},
        headers=backend_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "failed"
    assert updated.json()["error_message"] == "ipp backend exited 1"


def test_update_nonexistent_job_404(client, backend_headers):
    response = client.patch(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000",
        json={"status": "forwarded"},
        headers=backend_headers,
    )
    assert response.status_code == 404


def test_internal_connection_lookup(client, printer_id, backend_headers):
    response = client.get(
        f"/api/v1/internal/printers/{printer_id}/connection", headers=backend_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ip_address"] == "10.0.0.9"
    assert body["name"] == "Test Printer"


def test_internal_connection_lookup_requires_token(client, printer_id):
    response = client.get(f"/api/v1/internal/printers/{printer_id}/connection")
    assert response.status_code == 401


def test_list_jobs_requires_auth(client, printer_id, backend_headers):
    client.post("/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers)
    response = client.get("/api/v1/jobs")
    assert response.status_code == 401


def test_list_jobs_returns_newest_first_with_printer_name(client, printer_id, backend_headers, auth_headers):
    # SQLite's server_default=func.now() only has second-level resolution, so
    # two jobs created back-to-back in a test can legitimately tie on
    # created_at — Postgres (production) has microsecond precision and won't.
    # Assert presence/shape here rather than tie-breaking order.
    first = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id, "submitted_by": "adele"}, headers=backend_headers
    )
    second = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id, "submitted_by": "bob"}, headers=backend_headers
    )

    response = client.get("/api/v1/jobs", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert {job["id"] for job in body} == {second.json()["id"], first.json()["id"]}
    assert body[0]["created_at"] >= body[1]["created_at"]
    assert all(job["printer_name"] == "Test Printer" for job in body)


def test_list_jobs_filters_by_printer(
    client, printer_id, backend_headers, auth_headers, mock_failed_probe
):
    other = client.post(
        "/api/v1/printers",
        json={"name": "Other Printer", "ip_address": "10.0.0.10"},
        headers=auth_headers,
    )
    other_printer_id = other.json()["id"]

    client.post("/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers)
    client.post("/api/v1/jobs", json={"printer_id": other_printer_id}, headers=backend_headers)

    response = client.get(f"/api/v1/jobs?printer_id={printer_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["printer_id"] == printer_id


def test_list_jobs_caps_limit(client, printer_id, backend_headers, auth_headers):
    for _ in range(3):
        client.post("/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers)

    response = client.get("/api/v1/jobs?limit=2", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_create_job_attributes_via_trusted_cups_user(client, printer_id, backend_headers):
    response = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": "jdoe"},
        headers=backend_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["submitted_by"] == "jdoe"
    assert body["attribution_method"] == "cups"


def test_create_job_without_submitted_by_is_unresolved(client, printer_id, backend_headers):
    response = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "source_host": "10.0.0.99"},
        headers=backend_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["submitted_by"] == "unknown"
    assert body["attribution_method"] == "unresolved"


def test_update_job_records_page_count(client, printer_id, backend_headers):
    create = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers
    )
    job_id = create.json()["id"]

    updated = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": "forwarded", "page_count": 7},
        headers=backend_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["page_count"] == 7


def test_update_job_without_page_count_stays_null(client, printer_id, backend_headers):
    create = client.post(
        "/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers
    )
    job_id = create.json()["id"]

    updated = client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": "forwarded"},
        headers=backend_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["page_count"] is None


async def test_job_usage_aggregates_per_user(
    client, printer_id, backend_headers, auth_headers, db_session_factory
):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(email="adele@example.com", name="Adele", synced_at=datetime.now(UTC))
        )
        session.add(
            GoogleWorkspaceUser(email="bob@example.com", name="Bob", synced_at=datetime.now(UTC))
        )
        session.add(
            GoogleWorkspaceUser(
                email="never.printed@example.com", name="Never Printed", synced_at=datetime.now(UTC)
            )
        )
        await session.commit()

    adele_job_1 = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": "adele@example.com", "file_size_bytes": 1000},
        headers=backend_headers,
    ).json()
    adele_job_2 = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": "adele@example.com", "file_size_bytes": 2000},
        headers=backend_headers,
    ).json()
    bob_job = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": "bob@example.com", "file_size_bytes": 500},
        headers=backend_headers,
    ).json()
    unmatched_job = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": "some.local.user", "file_size_bytes": 250},
        headers=backend_headers,
    ).json()

    client.patch(
        f"/api/v1/jobs/{adele_job_1['id']}",
        json={"status": "forwarded", "page_count": 3},
        headers=backend_headers,
    )
    client.patch(
        f"/api/v1/jobs/{adele_job_2['id']}",
        json={"status": "forwarded", "page_count": 5},
        headers=backend_headers,
    )
    client.patch(
        f"/api/v1/jobs/{bob_job['id']}",
        json={"status": "forwarded", "page_count": 1},
        headers=backend_headers,
    )
    client.patch(
        f"/api/v1/jobs/{unmatched_job['id']}",
        json={"status": "forwarded", "page_count": 2},
        headers=backend_headers,
    )

    response = client.get("/api/v1/jobs/usage", headers=auth_headers)
    assert response.status_code == 200
    rows = response.json()
    by_email = {row["email"]: row for row in rows if not row["is_other"]}

    assert by_email["adele@example.com"]["job_count"] == 2
    assert by_email["adele@example.com"]["total_pages"] == 8
    assert by_email["adele@example.com"]["total_bytes"] == 3000
    assert by_email["bob@example.com"]["job_count"] == 1
    assert by_email["bob@example.com"]["total_pages"] == 1

    never_printed = by_email["never.printed@example.com"]
    assert never_printed["job_count"] == 0
    assert never_printed["total_pages"] == 0
    assert never_printed["total_bytes"] == 0

    other_rows = [row for row in rows if row["is_other"]]
    assert len(other_rows) == 1
    assert other_rows[0]["job_count"] == 1
    assert other_rows[0]["total_pages"] == 2
    assert other_rows[0]["total_bytes"] == 250
    assert other_rows[0]["email"] is None


def test_job_usage_requires_auth(client, printer_id, backend_headers):
    client.post("/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers)
    response = client.get("/api/v1/jobs/usage")
    assert response.status_code == 401


def test_job_usage_forbidden_for_viewer(client, printer_id, backend_headers, monkeypatch):
    client.post("/api/v1/jobs", json={"printer_id": printer_id}, headers=backend_headers)

    google_settings = client.put(
        "/api/v1/settings/google-sso",
        headers={
            "Authorization": (
                "Bearer "
                + client.post(
                    "/auth/login", json={"username": "admin", "password": "changeme"}
                ).json()["access_token"]
            )
        },
        json={
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "workspace_domain": "example.org",
            "initial_admin_emails": [],
            "redirect_base_url": "https://printops.test",
            "enabled": True,
        },
    )
    assert google_settings.status_code == 200

    async def fake_exchange_code(**kwargs):
        return {"id_token": "fake-id-token"}

    def fake_verify_id_token(id_token, client_id):
        return {
            "sub": "google-sub-viewer",
            "email": "viewer@example.org",
            "email_verified": True,
            "hd": "example.org",
            "name": "Viewer Person",
            "picture": None,
        }

    monkeypatch.setattr("app.routers.auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.routers.auth.verify_id_token", fake_verify_id_token)

    login_response = client.get("/auth/google/login", follow_redirects=False)
    state = login_response.cookies["printops_oauth_state"]
    callback = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    viewer_token = callback.headers["location"].split("token=", 1)[1]

    response = client.get(
        "/api/v1/jobs/usage", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert response.status_code == 403
