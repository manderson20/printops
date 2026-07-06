from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.integrations import git_update
from app.main import app
from app.models.base import Base


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
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_version_is_public_to_any_authed_user(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "1.2.3")
    response = client.get("/api/v1/updates/version", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"version": "1.2.3"}


def test_version_requires_auth(client):
    response = client.get("/api/v1/updates/version")
    assert response.status_code == 401


def test_check_for_update_reports_available(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "1.0.0")
    monkeypatch.setattr("app.routers.updates.get_latest_version", lambda: "1.1.0")
    monkeypatch.setattr("app.routers.updates.get_changelog_section", lambda v: f"## [{v}]\n- new stuff")

    response = client.get("/api/v1/updates/check", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "current_version": "1.0.0",
        "latest_version": "1.1.0",
        "update_available": True,
        "changelog": "## [1.1.0]\n- new stuff",
    }


def test_check_for_update_reports_up_to_date_without_fetching_changelog(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "1.0.0")
    monkeypatch.setattr("app.routers.updates.get_latest_version", lambda: "1.0.0")

    def fail_if_called(v):
        raise AssertionError("changelog should not be fetched when already up to date")

    monkeypatch.setattr("app.routers.updates.get_changelog_section", fail_if_called)

    response = client.get("/api/v1/updates/check", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["update_available"] is False
    assert body["changelog"] is None


def test_check_for_update_not_available_when_origin_is_behind(client, auth_headers, monkeypatch):
    """Regression test: origin/main can legitimately be behind the running
    working tree (commits made/deployed locally, not yet pushed) — this
    must never be reported as an update, even though the versions differ."""
    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "0.12.0")
    monkeypatch.setattr("app.routers.updates.get_latest_version", lambda: "0.7.0")

    def fail_if_called(v):
        raise AssertionError("changelog should not be fetched when not actually an update")

    monkeypatch.setattr("app.routers.updates.get_changelog_section", fail_if_called)

    response = client.get("/api/v1/updates/check", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["update_available"] is False
    assert body["changelog"] is None


def test_check_for_update_surfaces_git_errors_as_502(client, auth_headers, monkeypatch):
    def raise_error():
        raise git_update.GitUpdateError("could not reach origin")

    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "1.0.0")
    monkeypatch.setattr("app.routers.updates.get_latest_version", raise_error)

    response = client.get("/api/v1/updates/check", headers=auth_headers)
    assert response.status_code == 502


def test_check_for_update_forbidden_for_viewer(client, monkeypatch):
    monkeypatch.setattr("app.routers.updates.get_current_version", lambda: "1.0.0")
    monkeypatch.setattr("app.routers.updates.get_latest_version", lambda: "1.0.0")

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
        "/api/v1/updates/check", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert response.status_code == 403


def test_schedule_update_creates_pending_row(client, auth_headers):
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    response = client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": scheduled_at, "target_version": "1.1.0"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["target_version"] == "1.1.0"
    assert body["status"] == "pending"
    assert body["requested_by"] == "admin"


def test_schedule_update_upserts_existing_pending_row(client, auth_headers):
    first_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    second_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    first = client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": first_time, "target_version": "1.1.0"},
        headers=auth_headers,
    ).json()
    second = client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": second_time, "target_version": "1.2.0"},
        headers=auth_headers,
    ).json()

    assert first["id"] == second["id"]
    assert second["target_version"] == "1.2.0"

    history = client.get("/api/v1/updates/schedule", headers=auth_headers).json()
    assert len(history) == 1


def test_schedule_update_rejects_while_in_progress(client, auth_headers, backend_headers):
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": scheduled_at, "target_version": "1.1.0"},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/updates/complete",
        json={"status": "in_progress"},
        headers=backend_headers,
    )

    response = client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": scheduled_at, "target_version": "1.1.0"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_cancel_schedule_marks_failed(client, auth_headers):
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": scheduled_at, "target_version": "1.1.0"},
        headers=auth_headers,
    ).json()

    response = client.delete(f"/api/v1/updates/schedule/{created['id']}", headers=auth_headers)
    assert response.status_code == 204

    history = client.get("/api/v1/updates/schedule", headers=auth_headers).json()
    assert history[0]["status"] == "failed"
    assert history[0]["log"] == "Cancelled by admin."


def test_cancel_nonexistent_schedule_404s(client, auth_headers):
    response = client.delete(
        "/api/v1/updates/schedule/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert response.status_code == 404


def test_status_returns_null_when_nothing_pending(client, backend_headers):
    response = client.get("/api/v1/updates/status", headers=backend_headers)
    assert response.status_code == 200
    assert response.json() == {"pending": None}


def test_status_requires_backend_token(client):
    response = client.get("/api/v1/updates/status")
    assert response.status_code == 401


def test_status_and_complete_round_trip(client, auth_headers, backend_headers):
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    client.post(
        "/api/v1/updates/schedule",
        json={"scheduled_at": scheduled_at, "target_version": "1.1.0"},
        headers=auth_headers,
    )

    status_response = client.get("/api/v1/updates/status", headers=backend_headers)
    assert status_response.json()["pending"]["target_version"] == "1.1.0"

    complete_response = client.post(
        "/api/v1/updates/complete",
        json={"status": "completed", "log": "all good"},
        headers=backend_headers,
    )
    assert complete_response.status_code == 204

    history = client.get("/api/v1/updates/schedule", headers=auth_headers).json()
    assert history[0]["status"] == "completed"
    assert history[0]["log"] == "all good"
    assert history[0]["completed_at"] is not None

    # Once terminal, it's no longer "pending" from the watcher's perspective.
    status_response = client.get("/api/v1/updates/status", headers=backend_headers)
    assert status_response.json() == {"pending": None}


def test_complete_with_nothing_pending_is_a_noop(client, backend_headers):
    response = client.post(
        "/api/v1/updates/complete",
        json={"status": "completed", "log": "stray call"},
        headers=backend_headers,
    )
    assert response.status_code == 204
