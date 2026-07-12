import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.server_sync import ServerSyncError
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.printers import discovery as printer_discovery
from app.printers.ipp_client import PrinterProbeError
from app.routers import printers as printers_router
from app.routers import settings as settings_router


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
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


@pytest.fixture(autouse=True)
def mock_server_sync(monkeypatch):
    """Default to a no-op success so tests don't try to actually run
    scripts/sync_server_settings.sh — same convention as test_printers_api.py's
    mock_queue_sync. Tests exercising sync failure override this again."""
    monkeypatch.setattr(settings_router, "sync_server_settings", lambda: None)


@pytest.fixture(autouse=True)
def mock_certificate_status(monkeypatch):
    """read_certificate_status() defaults to a real filesystem path
    (app/core/tls_status.py:MANAGED_CERT_PATH) — without this, tests here
    pick up whatever real cert scripts/sync_server_settings.sh happens to
    have already synced on whatever box runs the suite (confirmed: this
    broke on the exact box that had just done a live TLS deployment).
    Defaults to "nothing synced yet"; tests exercising the cert-found path
    override this explicitly."""
    monkeypatch.setattr(settings_router, "read_certificate_status", lambda: None)


@pytest.fixture(autouse=True)
def mock_printer_queue_sync(monkeypatch):
    """This file creates printers (test_internal_printer_ids_excludes_archived)
    — must not let that shell out to the real scripts/sync_cups_queue.sh on
    this box, same convention as test_printers_api.py's mock_queue_sync."""
    monkeypatch.setattr(printers_router, "sync_queue", lambda printer_id, is_virtual=False: None)
    monkeypatch.setattr(printers_router, "remove_queue", lambda printer_id, is_virtual=False: None)


@pytest.fixture(autouse=True)
def mock_printer_probe(monkeypatch):
    """Avoids a real (slow, doomed-to-fail) network probe against the fake
    test IPs used below — same fixture shape as test_printers_api.py's
    mock_failed_probe."""

    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        raise PrinterProbeError(f"Could not reach an IPP printer at {ip_address}: timed out")

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer)


def test_get_seeds_hostname_from_env_default(client, auth_headers):
    response = client.get("/api/v1/settings/server", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["hostname"] == get_settings().print_server_host
    assert body["require_encryption"] is False
    assert body["advertise_ipps"] is False
    assert body["sync_error"] is None
    assert body["certificate"] is None


def test_get_requires_some_auth(client):
    # The whole settings router requires a logged-in user (any role) —
    # matches every other GET here (Zabbix, SNMP, ...); PUT additionally
    # requires admin (below).
    response = client.get("/api/v1/settings/server")
    assert response.status_code == 401


def test_get_works_for_any_logged_in_user(client, auth_headers):
    response = client.get("/api/v1/settings/server", headers=auth_headers)
    assert response.status_code == 200


def test_put_requires_admin(client):
    response = client.put("/api/v1/settings/server", json={"hostname": "print.example.org"})
    assert response.status_code == 401


def test_put_commits_before_invoking_sync(client, auth_headers, db_session_factory, monkeypatch):
    """Regression test: sync_server_settings() shells out to a script that
    reads settings back over its own HTTP request (a separate DB session/
    connection) — confirmed live that calling it *before* this request's
    own commit means that script sees the stale, pre-update value. A fresh
    session opened from inside the sync callback simulates that separate
    connection."""
    seen_hostname = {}

    async def _check_committed():
        async with db_session_factory() as session:
            from app.server_settings.service import get_or_create_server_settings

            settings = await get_or_create_server_settings(session)
            seen_hostname["value"] = settings.hostname

    def fake_sync():
        import asyncio

        asyncio.run(_check_committed())

    monkeypatch.setattr(settings_router, "sync_server_settings", fake_sync)
    client.put(
        "/api/v1/settings/server", headers=auth_headers, json={"hostname": "committed.example.org"}
    )
    assert seen_hostname["value"] == "committed.example.org"


def test_sync_now_requires_admin(client):
    response = client.post("/api/v1/settings/server/sync")
    assert response.status_code == 401


def test_sync_now_reruns_without_changing_fields(client, auth_headers):
    client.put("/api/v1/settings/server", headers=auth_headers, json={"hostname": "a.example.org"})
    response = client.post("/api/v1/settings/server/sync", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["hostname"] == "a.example.org"


def test_sync_now_records_failure(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        settings_router,
        "sync_server_settings",
        lambda: (_ for _ in ()).throw(ServerSyncError("cert not found")),
    )
    response = client.post("/api/v1/settings/server/sync", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["sync_error"] == "cert not found"


def test_put_updates_hostname_and_toggles(client, auth_headers):
    response = client.put(
        "/api/v1/settings/server",
        headers=auth_headers,
        json={"hostname": "print.example.org", "require_encryption": True, "advertise_ipps": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hostname"] == "print.example.org"
    assert body["require_encryption"] is True
    assert body["advertise_ipps"] is True


def test_put_partial_update_leaves_other_fields_unchanged(client, auth_headers):
    client.put(
        "/api/v1/settings/server",
        headers=auth_headers,
        json={"hostname": "print.example.org", "advertise_ipps": True},
    )
    response = client.put(
        "/api/v1/settings/server", headers=auth_headers, json={"require_encryption": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hostname"] == "print.example.org"
    assert body["advertise_ipps"] is True
    assert body["require_encryption"] is True


def test_put_records_sync_error_non_fatally(client, auth_headers, monkeypatch):
    def failing_sync():
        raise ServerSyncError("cupsd.conf not writable")

    monkeypatch.setattr(settings_router, "sync_server_settings", failing_sync)
    response = client.put(
        "/api/v1/settings/server", headers=auth_headers, json={"hostname": "print.example.org"}
    )
    # The save itself still succeeds — only the sync is best-effort.
    assert response.status_code == 200
    body = response.json()
    assert body["hostname"] == "print.example.org"
    assert body["sync_error"] == "cupsd.conf not writable"


def test_put_clears_prior_sync_error_on_next_success(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        settings_router,
        "sync_server_settings",
        lambda: (_ for _ in ()).throw(ServerSyncError("boom")),
    )
    client.put("/api/v1/settings/server", headers=auth_headers, json={"hostname": "a.example.org"})

    monkeypatch.setattr(settings_router, "sync_server_settings", lambda: None)
    response = client.put(
        "/api/v1/settings/server", headers=auth_headers, json={"hostname": "b.example.org"}
    )
    assert response.json()["sync_error"] is None


def test_internal_server_settings_for_sync_script(client, auth_headers, backend_headers):
    client.put(
        "/api/v1/settings/server",
        headers=auth_headers,
        json={"hostname": "print.example.org", "require_encryption": True},
    )
    response = client.get("/api/v1/internal/server-settings", headers=backend_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "hostname": "print.example.org",
        "require_encryption": True,
        "advertise_ipps": False,
    }


def test_internal_server_settings_requires_backend_token(client):
    response = client.get("/api/v1/internal/server-settings")
    assert response.status_code == 401


def test_internal_printer_ids_excludes_archived(client, auth_headers, backend_headers):
    active = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Active Printer", "ip_address": "10.0.0.50"},
    ).json()
    archived = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Archived Printer", "ip_address": "10.0.0.51"},
    ).json()
    client.post(f"/api/v1/printers/{archived['id']}/archive", headers=auth_headers)

    response = client.get("/api/v1/internal/printers/ids", headers=backend_headers)
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert active["id"] in ids
    assert archived["id"] not in ids


def test_internal_printer_ids_requires_backend_token(client):
    response = client.get("/api/v1/internal/printers/ids")
    assert response.status_code == 401
