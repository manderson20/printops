import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer

GOOGLE_CLAIMS = {
    "sub": "google-sub-viewer",
    "email": "viewer@example.org",
    "email_verified": True,
    "hd": "example.org",
    "name": "Viewer Person",
    "picture": None,
}


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
def admin_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def google_settings(client, admin_headers):
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=admin_headers,
        json={
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "workspace_domain": "example.org",
            "initial_admin_emails": [],
            "redirect_base_url": "https://printops.test",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def viewer_headers(client, google_settings, monkeypatch):
    async def fake_exchange_code(**kwargs):
        return {"id_token": "fake-id-token"}

    def fake_verify_id_token(id_token, client_id):
        return GOOGLE_CLAIMS

    monkeypatch.setattr("app.routers.auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.routers.auth.verify_id_token", fake_verify_id_token)

    login_response = client.get("/auth/google/login", follow_redirects=False)
    state = login_response.cookies["printops_oauth_state"]
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    token = response.headers["location"].split("token=", 1)[1]
    return {"Authorization": f"Bearer {token}"}


def _make_job(client, printer_id, backend_headers, submitted_by, page_count, status="forwarded"):
    create = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "submitted_by": submitted_by},
        headers=backend_headers,
    )
    job_id = create.json()["id"]
    client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": status, "page_count": page_count},
        headers=backend_headers,
    )
    return job_id


def test_summary_requires_auth(client):
    response = client.get("/api/v1/reports/summary")
    assert response.status_code == 401


def test_summary_totals_pages_and_jobs(client, printer_id, backend_headers, admin_headers):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)
    _make_job(client, printer_id, backend_headers, "bob@example.org", 5)

    response = client.get("/api/v1/reports/summary", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total_jobs"] == 2
    assert body["total_pages"] == 15
    assert body["forwarded_jobs"] == 2


def test_viewer_only_sees_own_jobs_in_summary(
    client, printer_id, backend_headers, admin_headers, viewer_headers
):
    _make_job(client, printer_id, backend_headers, "viewer@example.org", 10)
    _make_job(client, printer_id, backend_headers, "someone.else@example.org", 100)

    admin_view = client.get("/api/v1/reports/summary", headers=admin_headers).json()
    assert admin_view["total_jobs"] == 2
    assert admin_view["total_pages"] == 110

    viewer_view = client.get("/api/v1/reports/summary", headers=viewer_headers).json()
    assert viewer_view["total_jobs"] == 1
    assert viewer_view["total_pages"] == 10


def test_viewer_cannot_override_submitted_by_filter(
    client, printer_id, backend_headers, viewer_headers
):
    _make_job(client, printer_id, backend_headers, "someone.else@example.org", 100)

    response = client.get(
        "/api/v1/reports/summary?submitted_by=someone.else@example.org", headers=viewer_headers
    )
    assert response.status_code == 200
    # Still scoped to the viewer's own identity, ignoring the passed filter.
    assert response.json()["total_jobs"] == 0


def test_timeline_rejects_bad_granularity(client, admin_headers):
    response = client.get("/api/v1/reports/timeline?granularity=fortnight", headers=admin_headers)
    assert response.status_code == 400


def test_leaderboard_by_printer(client, printer_id, backend_headers, admin_headers):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)

    response = client.get("/api/v1/reports/leaderboard?type=printer", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body[0]["label"] == "Test Printer"
    assert body[0]["job_count"] == 1


def test_fun_facts_smoke(client, printer_id, backend_headers, admin_headers):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)

    response = client.get("/api/v1/reports/fun-facts", headers=admin_headers)
    assert response.status_code == 200
    assert "facts" in response.json()


def test_export_csv_contains_job_row(client, printer_id, backend_headers, admin_headers):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)

    response = client.get("/api/v1/reports/export.csv", headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.text
    assert "alice@example.org" in body
    assert body.startswith("job_id,")


def test_snapshot_create_and_read_is_admin_only(
    client, printer_id, backend_headers, admin_headers, viewer_headers
):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)

    forbidden = client.post(
        "/api/v1/reports/snapshots",
        headers=viewer_headers,
        json={"name": "March", "range_start": "2026-01-01", "range_end": "2026-12-31"},
    )
    assert forbidden.status_code == 403

    created = client.post(
        "/api/v1/reports/snapshots",
        headers=admin_headers,
        json={"name": "March", "range_start": "2026-01-01", "range_end": "2026-12-31"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "March"
    assert body["totals"]["total_pages"] == 10
    assert body["created_by"] == "admin"

    fetched = client.get(f"/api/v1/reports/snapshots/{body['id']}", headers=admin_headers)
    assert fetched.status_code == 200
    assert fetched.json()["totals"]["total_pages"] == 10


def test_snapshot_freezes_totals_even_if_formulas_change_later(
    client, printer_id, backend_headers, admin_headers
):
    _make_job(client, printer_id, backend_headers, "alice@example.org", 10)

    created = client.post(
        "/api/v1/reports/snapshots",
        headers=admin_headers,
        json={"name": "Snap", "range_start": "2026-01-01", "range_end": "2026-12-31"},
    )
    original_cost = created.json()["totals"]["estimated_cost_total"]

    client.put(
        "/api/v1/settings/report-formulas",
        headers=admin_headers,
        json={"cost_per_page_mono": 99.0},
    )

    snapshot_id = created.json()["id"]
    refetched = client.get(f"/api/v1/reports/snapshots/{snapshot_id}", headers=admin_headers)
    assert refetched.json()["totals"]["estimated_cost_total"] == original_cost


def test_report_formula_settings_defaults_and_update(client, admin_headers):
    defaults = client.get("/api/v1/settings/report-formulas", headers=admin_headers)
    assert defaults.status_code == 200
    assert defaults.json()["cost_per_page_mono"] == 0.03

    updated = client.put(
        "/api/v1/settings/report-formulas",
        headers=admin_headers,
        json={"cost_per_page_color": 0.25},
    )
    assert updated.status_code == 200
    assert updated.json()["cost_per_page_color"] == 0.25
    assert updated.json()["cost_per_page_mono"] == 0.03


def test_report_formula_settings_requires_admin(client, viewer_headers):
    response = client.put(
        "/api/v1/settings/report-formulas",
        headers=viewer_headers,
        json={"cost_per_page_mono": 1.0},
    )
    assert response.status_code == 403
