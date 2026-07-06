import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base

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


@pytest.fixture
def admin_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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


def test_admin_can_list_users(client, admin_headers, viewer_headers):
    response = client.get("/api/v1/users", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert body["total"] >= 1
    emails = [u["email"] for u in body["items"]]
    assert "viewer@example.org" in emails


def test_admin_can_patch_user_role(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")

    response = client.patch(f"/api/v1/users/{user_id}", headers=admin_headers, json={"role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_viewer_cannot_list_users(client, viewer_headers):
    response = client.get("/api/v1/users", headers=viewer_headers)
    assert response.status_code == 403


def test_viewer_cannot_patch_users(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")

    response = client.patch(f"/api/v1/users/{user_id}", headers=viewer_headers, json={"role": "admin"})
    assert response.status_code == 403


def test_list_users_pagination(client, admin_headers, viewer_headers):
    response = client.get("/api/v1/users", headers=admin_headers, params={"page": 1, "page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["total"] >= 1


def test_list_users_search(client, admin_headers, viewer_headers):
    response = client.get("/api/v1/users", headers=admin_headers, params={"search": "viewer@example"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "viewer@example.org"

    response = client.get("/api/v1/users", headers=admin_headers, params={"search": "no-such-user"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
