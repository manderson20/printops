import io
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.google_workspace import GoogleWorkspaceUser
from app.routers import self_service_print as self_service_print_router

GOOGLE_CLAIMS = {
    "sub": "google-sub-viewer",
    "email": "viewer@example.org",
    "email_verified": True,
    "hd": "example.org",
    "name": "Viewer Person",
    "picture": None,
}

PDF_BYTES = b"%PDF-1.4\n%fake pdf content\n%%EOF"


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


@pytest.fixture
def google_settings(client, auth_headers):
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=auth_headers,
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


def _create_printer(client, auth_headers, name="Self-Service Target", ip="10.0.1.5"):
    response = client.post(
        "/api/v1/printers", headers=auth_headers, json={"name": name, "ip_address": ip}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_list_printers_requires_google_account(client, auth_headers):
    # The dev break-glass admin login has no email at all.
    response = client.get("/api/v1/self-service-print/printers", headers=auth_headers)
    assert response.status_code == 400


def test_list_printers_unrestricted_by_default(client, auth_headers, viewer_headers):
    _create_printer(client, auth_headers)
    response = client.get("/api/v1/self-service-print/printers", headers=viewer_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


async def _seed_workspace_user(db_session_factory, email: str, org_unit_path: str):
    async with db_session_factory() as session:
        session.add(
            GoogleWorkspaceUser(
                email=email,
                name=email,
                org_unit_path=org_unit_path,
                synced_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def test_list_printers_excludes_restricted_printer_for_non_matching_ou(
    client, auth_headers, viewer_headers, db_session_factory
):
    await _seed_workspace_user(db_session_factory, "viewer@example.org", "/Students/Grade9")
    await _seed_workspace_user(db_session_factory, "teacher@example.org", "/Employees/Teachers")

    printer_id = _create_printer(client, auth_headers)
    restrict = client.post(
        f"/api/v1/printers/{printer_id}/allowed-ous",
        headers=auth_headers,
        json={"ou_path": "/Employees/Teachers"},
    )
    assert restrict.status_code == 201, restrict.text

    response = client.get("/api/v1/self-service-print/printers", headers=viewer_headers)
    assert response.status_code == 200
    assert response.json() == []


async def test_list_printers_includes_restricted_printer_for_nested_ou_match(
    client, auth_headers, viewer_headers, db_session_factory
):
    await _seed_workspace_user(
        db_session_factory, "viewer@example.org", "/Employees/Teachers/Math"
    )
    await _seed_workspace_user(db_session_factory, "teacher@example.org", "/Employees/Teachers")

    printer_id = _create_printer(client, auth_headers)
    restrict = client.post(
        f"/api/v1/printers/{printer_id}/allowed-ous",
        headers=auth_headers,
        json={"ou_path": "/Employees/Teachers"},
    )
    assert restrict.status_code == 201, restrict.text

    response = client.get("/api/v1/self-service-print/printers", headers=viewer_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == printer_id


def test_submit_rejects_non_pdf(client, auth_headers, viewer_headers):
    printer_id = _create_printer(client, auth_headers)
    response = client.post(
        "/api/v1/self-service-print",
        headers=viewer_headers,
        data={"printer_id": printer_id, "copies": "1"},
        files={"file": ("doc.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_submit_rejects_oversized_file(client, auth_headers, viewer_headers, monkeypatch):
    printer_id = _create_printer(client, auth_headers)
    monkeypatch.setattr(self_service_print_router, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        "/api/v1/self-service-print",
        headers=viewer_headers,
        data={"printer_id": printer_id, "copies": "1"},
        files={"file": ("doc.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert response.status_code == 413


def test_submit_403s_for_disallowed_printer(client, auth_headers, viewer_headers, monkeypatch):
    printer_id = _create_printer(client, auth_headers)

    async def fake_may_print(db, pid, email):
        return False

    monkeypatch.setattr(self_service_print_router, "user_may_print_to", fake_may_print)
    response = client.post(
        "/api/v1/self-service-print",
        headers=viewer_headers,
        data={"printer_id": printer_id, "copies": "1"},
        files={"file": ("doc.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert response.status_code == 403


def test_submit_success(client, auth_headers, viewer_headers, monkeypatch):
    printer_id = _create_printer(client, auth_headers)
    monkeypatch.setattr(
        self_service_print_router,
        "submit_uploaded_print_job",
        lambda pid, raw_bytes, filename, email, copies: "request id is printops-xyz-1",
    )
    response = client.post(
        "/api/v1/self-service-print",
        headers=viewer_headers,
        data={"printer_id": printer_id, "copies": "2"},
        files={"file": ("doc.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["printer_id"] == printer_id
    assert body["filename"] == "doc.pdf"
    assert body["copies"] == 2


def test_submit_translates_lp_error(client, auth_headers, viewer_headers, monkeypatch):
    from app.self_service_print.service import SelfServicePrintError

    printer_id = _create_printer(client, auth_headers)

    def fake_submit(pid, raw_bytes, filename, email, copies):
        raise SelfServicePrintError("No CUPS queue exists for this printer yet")

    monkeypatch.setattr(self_service_print_router, "submit_uploaded_print_job", fake_submit)
    response = client.post(
        "/api/v1/self-service-print",
        headers=viewer_headers,
        data={"printer_id": printer_id, "copies": "1"},
        files={"file": ("doc.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
    )
    assert response.status_code == 502
    assert "No CUPS queue" in response.json()["detail"]
