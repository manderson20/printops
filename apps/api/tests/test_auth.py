import time

import jwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base


def _decode(token: str) -> dict:
    return jwt.decode(token, "test-secret", algorithms=["HS256"])

GOOGLE_CLAIMS = {
    "sub": "google-sub-123",
    "email": "someone@example.org",
    "email_verified": True,
    "hd": "example.org",
    "name": "Some One",
    "picture": "https://example.org/pic.jpg",
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
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def google_settings(client, auth_headers):
    """Configures Google SSO the same way an admin would via the
    Integrations UI (PUT /api/v1/settings/google-sso), not env vars."""
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=auth_headers,
        json={
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "workspace_domain": "example.org",
            "initial_admin_emails": ["boss@example.org"],
            "redirect_base_url": "https://printops.test",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def viewer_headers(client, google_settings, monkeypatch):
    """Logs in a non-admin SSO viewer (someone@example.org — not on the
    initial-admin allowlist, see google_settings above) via the same
    stubbed Google flow the callback tests use."""
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    callback = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    token = callback.headers["location"].split("token=", 1)[1]
    return {"Authorization": f"Bearer {token}"}


def test_google_sso_settings_rejects_secret_matching_client_id(client, auth_headers):
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=auth_headers,
        json={"client_id": "same-value", "client_secret": "same-value"},
    )
    assert response.status_code == 422


def test_google_sso_settings_rejects_secret_shaped_like_client_id(client, auth_headers):
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=auth_headers,
        json={
            "client_id": "123-abc.apps.googleusercontent.com",
            "client_secret": "456-xyz.apps.googleusercontent.com",
        },
    )
    assert response.status_code == 422


def _stub_google(monkeypatch, claims=None):
    async def fake_exchange_code(**kwargs):
        return {"id_token": "fake-id-token"}

    def fake_verify_id_token(id_token, client_id):
        return claims if claims is not None else GOOGLE_CLAIMS

    monkeypatch.setattr("app.routers.auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.routers.auth.verify_id_token", fake_verify_id_token)


def _do_google_login(client) -> str:
    """Hits /auth/google/login and returns the state cookie it set. Passed
    explicitly on the callback request rather than relying on the
    TestClient's cookie jar — httpx's jar (correctly) won't replay a
    Secure-flagged cookie over the jar's plain http://testserver
    transport, unlike a real browser talking to the real HTTPS domain."""
    login_response = client.get("/auth/google/login", follow_redirects=False)
    return login_response.cookies["printops_oauth_state"]


def test_dev_login_yields_admin_role(client, auth_headers):
    response = client.get("/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"


def test_google_callback_creates_viewer_by_default(client, google_settings, monkeypatch):
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith("/login/callback#token=")
    assert "error" not in location

    token = location.split("token=", 1)[1]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "viewer"
    assert me.json()["email"] == "someone@example.org"


def test_google_callback_allowlisted_email_becomes_admin(client, google_settings, monkeypatch):
    _stub_google(
        monkeypatch, claims={**GOOGLE_CLAIMS, "sub": "google-sub-boss", "email": "boss@example.org"}
    )
    state = _do_google_login(client)
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    location = response.headers["location"]
    token = location.split("token=", 1)[1]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "admin"


def test_google_callback_rejects_wrong_domain(client, google_settings, monkeypatch):
    _stub_google(monkeypatch, claims={**GOOGLE_CLAIMS, "hd": "not-example.org"})
    state = _do_google_login(client)
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert "error=" in location
    assert "token=" not in location


def test_google_callback_rejects_bad_state(client, google_settings, monkeypatch):
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": "not-the-real-state"},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert "error=" in location


def test_google_callback_relogin_preserves_promoted_role(
    client, google_settings, monkeypatch, auth_headers
):
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    first = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    token = first.headers["location"].split("token=", 1)[1]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "viewer"

    users = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "someone@example.org")
    first_login_at = next(u["last_login_at"] for u in users if u["id"] == user_id)
    client.patch(f"/api/v1/users/{user_id}", headers=auth_headers, json={"role": "admin"})

    state2 = _do_google_login(client)
    second = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state2},
        cookies={"printops_oauth_state": state2},
        follow_redirects=False,
    )
    token2 = second.headers["location"].split("token=", 1)[1]
    me2 = client.get("/auth/me", headers={"Authorization": f"Bearer {token2}"})
    assert me2.json()["role"] == "admin"

    users_after = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    second_login_at = next(u["last_login_at"] for u in users_after if u["id"] == user_id)
    assert second_login_at != first_login_at


def test_refresh_requires_valid_token(client):
    response = client.post("/auth/refresh")
    assert response.status_code == 401


def test_refresh_dev_login_reissues_a_working_token(client, auth_headers):
    response = client.post("/auth/refresh", headers=auth_headers)
    assert response.status_code == 200
    new_token = response.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_refresh_uses_configured_idle_timeout(client, auth_headers):
    client.put(
        "/api/v1/settings/session", headers=auth_headers, json={"idle_timeout_minutes": 5}
    )
    response = client.post("/auth/refresh", headers=auth_headers)
    new_token = response.json()["access_token"]
    claims = _decode(new_token)

    remaining_minutes = (claims["exp"] - time.time()) / 60
    assert 4 <= remaining_minutes <= 5.5


def test_refresh_exempt_sso_user_gets_long_lived_token(
    client, auth_headers, google_settings, monkeypatch
):
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    callback = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    user_token = callback.headers["location"].split("token=", 1)[1]

    users = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "someone@example.org")
    marked = client.patch(
        f"/api/v1/users/{user_id}",
        headers=auth_headers,
        json={"exempt_from_timeout": True},
    )
    assert marked.json()["exempt_from_timeout"] is True

    client.put(
        "/api/v1/settings/session", headers=auth_headers, json={"idle_timeout_minutes": 5}
    )
    response = client.post(
        "/auth/refresh", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200
    claims = _decode(response.json()["access_token"])

    remaining_minutes = (claims["exp"] - time.time()) / 60
    # Far longer than the 5-minute configured idle timeout — the exempt
    # duration (24h), not SessionSettings.idle_timeout_minutes.
    assert remaining_minutes > 60


def test_refresh_revoking_exemption_takes_effect_next_refresh(
    client, auth_headers, google_settings, monkeypatch
):
    """Exemption is read fresh from the User row on every refresh, not
    baked into the token at login — revoking it should apply to the very
    next refresh, not wait for the token to fully expire."""
    _stub_google(monkeypatch)
    state = _do_google_login(client)
    callback = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    user_token = callback.headers["location"].split("token=", 1)[1]

    users = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    user_id = next(u["id"] for u in users if u["email"] == "someone@example.org")
    client.patch(
        f"/api/v1/users/{user_id}", headers=auth_headers, json={"exempt_from_timeout": True}
    )
    client.put(
        "/api/v1/settings/session", headers=auth_headers, json={"idle_timeout_minutes": 5}
    )

    first_refresh = client.post(
        "/auth/refresh", headers={"Authorization": f"Bearer {user_token}"}
    )
    refreshed_token = first_refresh.json()["access_token"]

    client.patch(
        f"/api/v1/users/{user_id}", headers=auth_headers, json={"exempt_from_timeout": False}
    )
    second_refresh = client.post(
        "/auth/refresh", headers={"Authorization": f"Bearer {refreshed_token}"}
    )
    claims = _decode(second_refresh.json()["access_token"])

    remaining_minutes = (claims["exp"] - time.time()) / 60
    assert remaining_minutes <= 5.5


def test_session_settings_default_and_requires_admin_to_change(
    client, auth_headers, viewer_headers
):
    response = client.get("/api/v1/settings/session", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["idle_timeout_minutes"] == 60

    forbidden = client.put(
        "/api/v1/settings/session", headers=viewer_headers, json={"idle_timeout_minutes": 10}
    )
    assert forbidden.status_code == 403
