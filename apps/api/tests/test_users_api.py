import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.impersonation import ImpersonationSession

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

    response = client.patch(
        f"/api/v1/users/{user_id}", headers=admin_headers, json={"role": "admin"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_admin_can_patch_exempt_from_timeout(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")
    assert next(u["exempt_from_timeout"] for u in users if u["id"] == user_id) is False

    response = client.patch(
        f"/api/v1/users/{user_id}", headers=admin_headers, json={"exempt_from_timeout": True}
    )
    assert response.status_code == 200
    assert response.json()["exempt_from_timeout"] is True


def test_viewer_cannot_list_users(client, viewer_headers):
    response = client.get("/api/v1/users", headers=viewer_headers)
    assert response.status_code == 403


def test_viewer_cannot_patch_users(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")

    response = client.patch(
        f"/api/v1/users/{user_id}", headers=viewer_headers, json={"role": "admin"}
    )
    assert response.status_code == 403


def test_list_users_pagination(client, admin_headers, viewer_headers):
    response = client.get(
        "/api/v1/users", headers=admin_headers, params={"page": 1, "page_size": 1}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["total"] >= 1


def test_list_users_search(client, admin_headers, viewer_headers):
    response = client.get(
        "/api/v1/users", headers=admin_headers, params={"search": "viewer@example"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["email"] == "viewer@example.org"

    response = client.get("/api/v1/users", headers=admin_headers, params={"search": "no-such-user"})
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_list_users_filters_by_role(client, admin_headers, viewer_headers):
    response = client.get("/api/v1/users", headers=admin_headers, params={"role": "admin"})
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()["items"]]
    assert "viewer@example.org" not in emails

    response = client.get("/api/v1/users", headers=admin_headers, params={"role": "viewer"})
    emails = [u["email"] for u in response.json()["items"]]
    assert "viewer@example.org" in emails


def test_admin_can_precreate_user(client, admin_headers):
    response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"email": "Future-Admin@Example.org", "role": "admin"},
    )
    assert response.status_code == 201
    body = response.json()
    # Stored lowercased — case-insensitive matching on first login (see
    # google_callback) assumes this.
    assert body["email"] == "future-admin@example.org"
    assert body["role"] == "admin"

    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    assert any(u["email"] == "future-admin@example.org" for u in users)


def test_precreate_user_rejects_duplicate_email(client, admin_headers, viewer_headers):
    response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"email": "viewer@example.org", "role": "admin"},
    )
    assert response.status_code == 409


async def test_admin_can_impersonate_viewer(
    client, admin_headers, viewer_headers, db_session_factory
):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")

    response = client.post(f"/api/v1/users/{viewer_id}/impersonate", headers=admin_headers)
    assert response.status_code == 200
    impersonation_token = response.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {impersonation_token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "viewer"
    assert body["email"] == "viewer@example.org"
    # The dev break-glass admin (admin_headers) has no email, so admin_email
    # falls back to its username — see impersonate_user's docstring.
    assert body["impersonated_by"] == "admin"

    async with db_session_factory() as session:
        result = await session.execute(select(ImpersonationSession))
        rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].admin_email == "admin"
    assert rows[0].admin_user_id is None  # dev break-glass has no User row
    assert rows[0].target_email == "viewer@example.org"
    assert rows[0].target_role == "viewer"


def test_viewer_cannot_impersonate(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")

    response = client.post(f"/api/v1/users/{viewer_id}/impersonate", headers=viewer_headers)
    assert response.status_code == 403


def test_cannot_impersonate_admin(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")
    client.patch(f"/api/v1/users/{viewer_id}", headers=admin_headers, json={"role": "admin"})

    response = client.post(f"/api/v1/users/{viewer_id}/impersonate", headers=admin_headers)
    assert response.status_code == 400


def test_cannot_impersonate_deactivated_account(client, admin_headers, viewer_headers):
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")
    client.patch(f"/api/v1/users/{viewer_id}", headers=admin_headers, json={"is_active": False})

    response = client.post(f"/api/v1/users/{viewer_id}/impersonate", headers=admin_headers)
    assert response.status_code == 400


def test_impersonation_token_cannot_mutate(client, admin_headers, viewer_headers):
    """block_impersonated_mutations (app/main.py) is the central read-only
    guarantee — proven here against an endpoint (POST /auth/refresh) that
    has nothing to do with impersonation itself, showing the block applies
    regardless of which route is targeted."""
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")
    impersonation_token = client.post(
        f"/api/v1/users/{viewer_id}/impersonate", headers=admin_headers
    ).json()["access_token"]

    response = client.post(
        "/auth/refresh", headers={"Authorization": f"Bearer {impersonation_token}"}
    )
    assert response.status_code == 403

    # A normal GET is unaffected — only mutating methods are blocked.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {impersonation_token}"})
    assert me.status_code == 200


def test_impersonation_token_cannot_mutate_with_lowercase_bearer_scheme(
    client, admin_headers, viewer_headers
):
    """Regression test: the read-only guard used to only recognize an
    exact "Bearer " prefix, but FastAPI's own OAuth2PasswordBearer accepts
    the scheme case-insensitively (get_current_user would still
    authenticate `authorization: bearer <token>` fine) — so a lowercase
    scheme used to sail straight through block_impersonated_mutations
    while still working for the actual request, defeating the read-only
    guarantee entirely."""
    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    viewer_id = next(u["id"] for u in users if u["email"] == "viewer@example.org")
    impersonation_token = client.post(
        f"/api/v1/users/{viewer_id}/impersonate", headers=admin_headers
    ).json()["access_token"]

    response = client.post(
        "/auth/refresh", headers={"Authorization": f"bearer {impersonation_token}"}
    )
    assert response.status_code == 403


def test_precreated_admin_becomes_admin_on_first_login(
    client, admin_headers, google_settings, monkeypatch
):
    """The core promise of pre-provisioning: granting a role before someone's
    first sign-in must actually take effect on that sign-in, by matching the
    pre-provisioned (google_sub=null) row by email instead of creating a
    second, orphaned row."""
    create_response = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"email": "new-admin@example.org", "role": "admin"},
    )
    assert create_response.status_code == 201
    precreated_id = create_response.json()["id"]

    async def fake_exchange_code(**kwargs):
        return {"id_token": "fake-id-token"}

    def fake_verify_id_token(id_token, client_id):
        return {
            "sub": "google-sub-new-admin",
            "email": "new-admin@example.org",
            "email_verified": True,
            "hd": "example.org",
            "name": "New Admin",
            "picture": None,
        }

    monkeypatch.setattr("app.routers.auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.routers.auth.verify_id_token", fake_verify_id_token)

    login_response = client.get("/auth/google/login", follow_redirects=False)
    state = login_response.cookies["printops_oauth_state"]
    callback_response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    token = callback_response.headers["location"].split("token=", 1)[1]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "admin"

    users = client.get("/api/v1/users", headers=admin_headers).json()["items"]
    matching = [u for u in users if u["email"] == "new-admin@example.org"]
    assert len(matching) == 1
    assert matching[0]["id"] == precreated_id
