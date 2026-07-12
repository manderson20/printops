import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.printers import discovery as printer_discovery
from app.printers import snmp_counters as snmp_counters_module
from app.printers import status as printer_status
from app.printers.ipp_client import PrinterProbeError, PrinterStateResult, ProbeResult
from app.printers.snmp_counters import DetectedSupply, SnmpProbeError
from app.routers import printers as printers_router
from app.routers import settings as settings_router

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
    # StaticPool keeps a single shared connection alive so the in-memory
    # SQLite DB isn't reset between requests (each would otherwise get its
    # own fresh :memory: database).
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


@pytest.fixture
def mock_successful_probe(monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        return ProbeResult(
            raw_attributes={
                "printer-make-and-model": "Mock MFP 3000",
                "color-supported": True,
                "sides-supported": ["one-sided", "two-sided-long-edge"],
                "finishings-supported": [4, 5],
            },
            resolved_path=ipp_path or "/ipp/print",
        )

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer)


@pytest.fixture
def mock_failed_probe(monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        raise PrinterProbeError("Could not reach an IPP printer at 10.0.0.5:631: timed out")

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer)


@pytest.fixture(autouse=True)
def mock_queue_sync(monkeypatch):
    """Every printer create/update now triggers a CUPS queue sync — default
    to a no-op success so existing tests don't try to actually run
    scripts/sync_cups_queue.sh. Tests exercising sync behavior itself
    override sync_queue/remove_queue again with their own monkeypatch."""
    monkeypatch.setattr(printers_router, "sync_queue", lambda printer_id, is_virtual=False: None)
    monkeypatch.setattr(printers_router, "remove_queue", lambda printer_id, is_virtual=False: None)


def test_create_requires_auth(client):
    response = client.post("/api/v1/printers", json={"name": "X", "ip_address": "10.0.0.5"})
    assert response.status_code == 401


def test_create_printer_success_discovers_capabilities(client, auth_headers, mock_successful_probe):
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Front Office MFP", "ip_address": "10.0.0.5", "building": "Main"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["model"] == "Mock MFP 3000"
    assert body["capabilities"]["color_supported"] is True
    assert body["capabilities"]["duplex_supported"] is True
    assert sorted(body["capabilities"]["finishings"]) == ["punch", "staple"]
    assert body["capabilities_error"] is None
    assert body["ipp_path"] == "/ipp/print"


def test_create_printer_offline_still_creates_record(client, auth_headers, mock_failed_probe):
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Offline Printer", "ip_address": "10.0.0.5"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["capabilities"] is None
    assert "Could not reach" in body["capabilities_error"]


def test_list_get_update_delete(client, auth_headers, mock_successful_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Lib Printer", "ip_address": "10.0.0.6"},
    )
    printer_id = create.json()["id"]

    listing = client.get("/api/v1/printers", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    get_one = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert get_one.status_code == 200

    patched = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"room": "204"}
    )
    assert patched.status_code == 200
    assert patched.json()["room"] == "204"

    deleted = client.delete(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert missing.status_code == 404


def test_rediscover_updates_capabilities(client, auth_headers, mock_failed_probe, monkeypatch):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Flaky Printer", "ip_address": "10.0.0.7"},
    )
    printer_id = create.json()["id"]
    assert create.json()["capabilities"] is None

    async def fake_probe_printer_now_online(
        ip_address, port=631, tls=False, timeout=5, ipp_path=None
    ):
        return ProbeResult(
            raw_attributes={"printer-make-and-model": "Now Online"}, resolved_path="/"
        )

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer_now_online)

    rediscovered = client.post(f"/api/v1/printers/{printer_id}/discover", headers=auth_headers)
    assert rediscovered.status_code == 200
    assert rediscovered.json()["capabilities_error"] is None
    assert rediscovered.json()["model"] == "Now Online"


def test_check_status_rediscovers_capabilities_on_reconnect(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    """A printer coming back online (e.g. after being swapped, or gaining a
    module, during a maintenance window) should get a fresh capability
    probe automatically — not just an updated status — without anyone
    clicking Rediscover. Covers app/printers/status.py's
    refresh_printer_status_and_rediscover, exercised via check-status the
    same way the 60s background loop uses it."""
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Swapped Printer", "ip_address": "10.0.0.8"},
    )
    printer_id = create.json()["id"]
    assert create.json()["capabilities"] is None

    async def offline_state(ip_address, port=631, tls=False, ipp_path=None, timeout=5):
        raise PrinterProbeError("Could not reach an IPP printer at 10.0.0.8:631: timed out")

    monkeypatch.setattr(printer_status, "probe_printer_state", offline_state)
    still_offline = client.post(f"/api/v1/printers/{printer_id}/check-status", headers=auth_headers)
    assert still_offline.json()["status"] == "offline"
    assert still_offline.json()["capabilities"] is None

    probe_calls = []

    async def now_online_state(ip_address, port=631, tls=False, ipp_path=None, timeout=5):
        return PrinterStateResult(printer_state=3, state_reasons=["none"], state_message=None)

    async def now_online_capabilities(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        probe_calls.append(ip_address)
        return ProbeResult(
            raw_attributes={"printer-make-and-model": "Swapped-In Model"}, resolved_path="/"
        )

    monkeypatch.setattr(printer_status, "probe_printer_state", now_online_state)
    monkeypatch.setattr(printer_discovery, "probe_printer", now_online_capabilities)

    reconnected = client.post(f"/api/v1/printers/{printer_id}/check-status", headers=auth_headers)
    assert reconnected.json()["status"] == "online"
    assert reconnected.json()["model"] == "Swapped-In Model"
    assert len(probe_calls) == 1

    # Already online -> already online again shouldn't re-trigger discovery.
    already_online = client.post(
        f"/api/v1/printers/{printer_id}/check-status", headers=auth_headers
    )
    assert already_online.json()["status"] == "online"
    assert len(probe_calls) == 1


def test_test_print_requires_auth(client, mock_failed_probe):
    response = client.post("/api/v1/printers/00000000-0000-0000-0000-000000000000/test-print")
    assert response.status_code == 401


def test_test_print_404s_for_missing_printer(client, auth_headers):
    response = client.post(
        "/api/v1/printers/00000000-0000-0000-0000-000000000000/test-print",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_test_print_success(client, auth_headers, mock_failed_probe, monkeypatch):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Test Print Target", "ip_address": "10.0.0.8"},
    )
    printer_id = create.json()["id"]

    monkeypatch.setattr(
        printers_router,
        "submit_test_print",
        lambda pid, name, user: "request id is printops-xyz-1 (1 file(s))",
    )

    response = client.post(f"/api/v1/printers/{printer_id}/test-print", headers=auth_headers)
    assert response.status_code == 200
    assert "request id" in response.json()["message"]


def test_test_print_translates_missing_queue_error(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    from app.printers.test_print import TestPrintError

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Unsynced Printer", "ip_address": "10.0.0.9"},
    )
    printer_id = create.json()["id"]

    def fake_submit(pid, name, user):
        raise TestPrintError("No CUPS queue exists for this printer yet")

    monkeypatch.setattr(printers_router, "submit_test_print", fake_submit)

    response = client.post(f"/api/v1/printers/{printer_id}/test-print", headers=auth_headers)
    assert response.status_code == 502
    assert "No CUPS queue" in response.json()["detail"]


def test_cups_queue_defaults_404s_for_missing_printer(client, auth_headers):
    response = client.get(
        "/api/v1/printers/00000000-0000-0000-0000-000000000000/cups-queue-defaults",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_cups_queue_defaults_requires_admin(
    client, auth_headers, viewer_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Viewer Cannot Check", "ip_address": "10.0.0.11"},
    )
    printer_id = create.json()["id"]

    response = client.get(
        f"/api/v1/printers/{printer_id}/cups-queue-defaults", headers=viewer_headers
    )
    assert response.status_code == 403


def test_cups_queue_defaults_success(client, auth_headers, mock_failed_probe, monkeypatch):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "CUPS Defaults Target", "ip_address": "10.0.0.12"},
    )
    printer_id = create.json()["id"]

    monkeypatch.setattr(
        printers_router, "get_cups_queue_default_page_size", lambda pid: "Letter"
    )

    response = client.get(
        f"/api/v1/printers/{printer_id}/cups-queue-defaults", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == {"page_size": "Letter"}


def test_cups_queue_defaults_none_when_unavailable(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Unsynced CUPS Target", "ip_address": "10.0.0.13"},
    )
    printer_id = create.json()["id"]

    monkeypatch.setattr(printers_router, "get_cups_queue_default_page_size", lambda pid: None)

    response = client.get(
        f"/api/v1/printers/{printer_id}/cups-queue-defaults", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json() == {"page_size": None}


def test_mdm_connection_info(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Lobby Printer", "ip_address": "10.0.0.11"},
    )
    printer_id = create.json()["id"]

    response = client.get(f"/api/v1/printers/{printer_id}/mdm-connection", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["queue_name"] == f"printops-{printer_id}"
    assert body["resource_path"] == f"/printers/printops-{printer_id}"
    assert body["ipp_uri"] == f"ipp://{body['host']}:{body['port']}{body['resource_path']}"
    assert body["airprint_enabled"] is False


def test_mdm_connection_uses_server_settings_hostname(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    """Regression test: mdm-connection used to read the static env-only
    Settings.print_server_host, so a hostname change on Settings > Server
    (app/models/server_settings.py) never actually reached newly-generated
    MDM connection info — confirmed live the queue kept advertising the
    raw IP after the domain was configured."""
    monkeypatch.setattr(settings_router, "sync_server_settings", lambda: None)
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Lobby Printer 2", "ip_address": "10.0.0.12"},
    )
    printer_id = create.json()["id"]

    client.put(
        "/api/v1/settings/server",
        headers=auth_headers,
        json={"hostname": "print.example.org"},
    )

    response = client.get(f"/api/v1/printers/{printer_id}/mdm-connection", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "print.example.org"
    assert body["ipp_uri"].startswith("ipp://print.example.org:")


def test_mdm_connection_requires_auth(client):
    response = client.get("/api/v1/printers/00000000-0000-0000-0000-000000000000/mdm-connection")
    assert response.status_code == 401


def test_create_printer_syncs_queue(client, auth_headers, mock_failed_probe, monkeypatch):
    calls = []
    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: calls.append(printer_id),
    )

    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "New Printer", "ip_address": "10.0.0.12"},
    )
    assert response.status_code == 201
    body = response.json()
    assert calls == [body["id"]]
    assert body["queue_sync_error"] is None


def test_create_printer_records_queue_sync_error(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    from app.printers.queue_sync import QueueSyncError

    def fake_sync_queue(printer_id, is_virtual=False):
        raise QueueSyncError("Unknown destination")

    monkeypatch.setattr(printers_router, "sync_queue", fake_sync_queue)

    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Broken Printer", "ip_address": "10.0.0.13"},
    )
    assert response.status_code == 201
    assert response.json()["queue_sync_error"] == "Unknown destination"


def test_update_printer_resyncs_only_on_queue_affecting_fields(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: calls.append(printer_id),
    )

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Editable Printer", "ip_address": "10.0.0.14"},
    )
    printer_id = create.json()["id"]
    calls.clear()  # ignore the create-time sync

    notes_only = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"notes": "some notes"}
    )
    assert notes_only.status_code == 200
    assert calls == []

    ip_change = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"ip_address": "10.0.0.99"},
    )
    assert ip_change.status_code == 200
    assert calls == [printer_id]


def test_delete_printer_removes_queue(client, auth_headers, mock_failed_probe, monkeypatch):
    calls = []
    monkeypatch.setattr(
        printers_router, "remove_queue", lambda printer_id: calls.append(printer_id)
    )

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Doomed Printer", "ip_address": "10.0.0.15"},
    )
    printer_id = create.json()["id"]

    response = client.delete(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert response.status_code == 204
    assert calls == [printer_id]


def test_resync_queue_clears_prior_error(client, auth_headers, mock_failed_probe, monkeypatch):
    from app.printers.queue_sync import QueueSyncError

    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: (_ for _ in ()).throw(
            QueueSyncError("printer offline")
        ),
    )
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Recovering Printer", "ip_address": "10.0.0.16"},
    )
    printer_id = create.json()["id"]
    assert create.json()["queue_sync_error"] == "printer offline"

    monkeypatch.setattr(printers_router, "sync_queue", lambda printer_id, is_virtual=False: None)
    response = client.post(f"/api/v1/printers/{printer_id}/resync-queue", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["queue_sync_error"] is None


async def test_apply_queue_sync_survives_printer_deleted_mid_flight(monkeypatch):
    """Confirmed live: sync_cups_queue.sh's bounded-timeout + generic-PPD
    fallback (for devices that can't handle -m everywhere's full attribute
    probe) can push a single sync well past 90s across both CUPS scripts —
    long enough for an admin to legitimately delete the printer while it's
    still in flight. The commit at the end must not crash the request with
    an unhandled StaleDataError once there's no row left to update."""
    from sqlalchemy.orm.exc import StaleDataError

    from app.models.printer import Printer
    from app.routers.printers import _apply_queue_sync

    class FakeSession:
        def __init__(self):
            self.rolled_back = False

        async def commit(self):
            raise StaleDataError("stale")

        async def rollback(self):
            self.rolled_back = True

    monkeypatch.setattr(printers_router, "sync_queue", lambda printer_id, is_virtual=False: None)
    printer = Printer(name="t", ip_address="10.0.0.1")
    fake_db = FakeSession()

    await _apply_queue_sync(printer, fake_db)  # must not raise
    assert fake_db.rolled_back is True


def test_enabling_release_required_generates_a_token(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Kiosk Printer", "ip_address": "10.0.0.20"},
    )
    printer_id = create.json()["id"]
    assert create.json()["release_token"] is None

    updated = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"release_required": True}
    )
    assert updated.status_code == 200
    assert updated.json()["release_required"] is True
    assert updated.json()["release_token"] is not None


def test_toggling_release_required_does_not_change_existing_token(
    client, auth_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Kiosk Printer", "ip_address": "10.0.0.20"},
    )
    printer_id = create.json()["id"]
    first = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"release_required": True}
    )
    token = first.json()["release_token"]

    second = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"release_required": False}
    )
    assert second.json()["release_token"] == token


def test_regenerate_release_token_rotates_it(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Kiosk Printer", "ip_address": "10.0.0.20"},
    )
    printer_id = create.json()["id"]
    enabled = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"release_required": True}
    )
    original_token = enabled.json()["release_token"]

    regenerated = client.post(
        f"/api/v1/printers/{printer_id}/regenerate-release-token", headers=auth_headers
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["release_token"] != original_token


def test_regenerate_release_token_requires_admin(client, mock_failed_probe):
    url = "/api/v1/printers/00000000-0000-0000-0000-000000000000/regenerate-release-token"
    response = client.post(url)
    assert response.status_code == 401


def test_enabling_release_required_resyncs_the_queue(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: calls.append(printer_id),
    )

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Kiosk Printer", "ip_address": "10.0.0.20"},
    )
    printer_id = create.json()["id"]
    calls.clear()  # ignore the create-time sync

    updated = client.patch(
        f"/api/v1/printers/{printer_id}", headers=auth_headers, json={"release_required": True}
    )
    assert updated.status_code == 200
    assert calls == [printer_id]


def test_create_printer_with_snmp_community_never_echoes_it(
    client, auth_headers, mock_failed_probe
):
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30", "snmp_community": "s3cret"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["has_snmp_community"] is True
    assert "s3cret" not in response.text


def test_create_printer_snmp_enabled_defaults_true(client, auth_headers, mock_failed_probe):
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    assert response.json()["snmp_enabled"] is True
    assert response.json()["has_snmp_community"] is False


def test_update_printer_snmp_community_never_echoed(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"snmp_community": "another-secret", "snmp_version": "v1", "snmp_port": 1610},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_snmp_community"] is True
    assert body["snmp_version"] == "v1"
    assert body["snmp_port"] == 1610
    assert "another-secret" not in response.text


def test_update_printer_blank_string_clears_snmp_overrides(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={
            "snmp_community": "a-secret",
            "snmp_version": "v1",
            "snmp_port": 1610,
            "snmp_vendor_profile": "canon",
        },
    )

    cleared = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={
            "snmp_community": "",
            "snmp_version": "",
            "snmp_port": None,
            "snmp_vendor_profile": "",
        },
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["has_snmp_community"] is False
    assert body["snmp_version"] is None
    assert body["snmp_port"] is None


def test_update_printer_ldap_bind_password_never_echoed(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "LDAP Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]

    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={
            "ldap_enabled": True,
            "ldap_bind_username": "ldap-printer",
            "ldap_bind_password": "top-secret",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ldap_enabled"] is True
    assert body["ldap_bind_username"] == "ldap-printer"
    assert body["has_ldap_bind_password"] is True
    assert "top-secret" not in response.text


def test_web_login_and_scan_credentials_never_echoed_on_write(
    client, auth_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Reference Creds Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]

    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={
            "web_login_username": "admin",
            "web_login_password": "web-secret",
            "scan_email_address": "cletus-scan@district.org",
            "scan_password": "scan-secret",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["web_login_username"] == "admin"
    assert body["scan_email_address"] == "cletus-scan@district.org"
    assert body["has_web_login_password"] is True
    assert body["has_scan_password"] is True
    assert body["web_login_password"] is None  # never echoed on the write response
    assert body["scan_password"] is None
    assert "web-secret" not in response.text
    assert "scan-secret" not in response.text


def test_admin_can_read_back_web_login_and_scan_passwords(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Reference Creds Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]
    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"web_login_password": "web-secret", "scan_password": "scan-secret"},
    )

    response = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["web_login_password"] == "web-secret"
    assert body["scan_password"] == "scan-secret"


def test_viewer_never_sees_web_login_or_scan_passwords(
    client, auth_headers, viewer_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Reference Creds Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]
    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"web_login_password": "web-secret", "scan_password": "scan-secret"},
    )

    response = client.get(f"/api/v1/printers/{printer_id}", headers=viewer_headers)
    assert response.status_code == 200
    body = response.json()
    # A viewer still learns *that* credentials are configured...
    assert body["has_web_login_password"] is True
    assert body["has_scan_password"] is True
    # ...but never the real values.
    assert body["web_login_password"] is None
    assert body["scan_password"] is None
    assert "web-secret" not in response.text
    assert "scan-secret" not in response.text


def test_list_printers_never_includes_plaintext_passwords_even_for_admin(
    client, auth_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Reference Creds Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]
    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"web_login_password": "web-secret", "scan_password": "scan-secret"},
    )

    response = client.get("/api/v1/printers", headers=auth_headers)
    assert response.status_code == 200
    entry = next(p for p in response.json() if p["id"] == printer_id)
    assert entry["has_web_login_password"] is True
    assert entry["web_login_password"] is None
    assert entry["scan_password"] is None
    assert "web-secret" not in response.text
    assert "scan-secret" not in response.text


def test_blank_string_clears_web_login_and_scan_passwords(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Reference Creds Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]
    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"web_login_password": "web-secret", "scan_password": "scan-secret"},
    )

    cleared = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"web_login_password": "", "scan_password": ""},
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["has_web_login_password"] is False
    assert body["has_scan_password"] is False


def test_update_printer_blank_string_clears_ldap_overrides(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "LDAP Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]

    client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={
            "ldap_enabled": True,
            "ldap_bind_username": "ldap-printer",
            "ldap_bind_password": "top-secret",
        },
    )

    cleared = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"ldap_bind_username": "", "ldap_bind_password": ""},
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["ldap_bind_username"] is None
    assert body["has_ldap_bind_password"] is False
    assert body["snmp_vendor_profile"] is None


def test_check_counters_returns_updated_printer(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    async def fake_refresh_printer_counters(printer, defaults):
        printer.page_count_total = 4242
        printer.page_count_confidence = "unsupported"

    monkeypatch.setattr(printers_router, "refresh_printer_counters", fake_refresh_printer_counters)

    response = client.post(f"/api/v1/printers/{printer_id}/check-counters", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["page_count_total"] == 4242
    assert body["page_count_confidence"] == "unsupported"


def test_check_counters_requires_some_auth(client, mock_failed_probe):
    """check-counters isn't admin-gated (read-only telemetry, matching
    check-status) but the router still requires a logged-in session —
    entirely unauthenticated calls are rejected."""
    url = "/api/v1/printers/00000000-0000-0000-0000-000000000000/check-counters"
    response = client.post(url)
    assert response.status_code == 401


def test_check_counters_records_a_reading_on_success(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    # Simulate the two calls landing on different calendar days — real
    # readings a day apart, not two calls a few milliseconds apart in the
    # test, since same-day readings collapse to the day's last value only
    # (see app/printers/counter_history.py's module docstring).
    from datetime import UTC, datetime, timedelta

    simulated_times = iter([datetime.now(UTC) - timedelta(days=1), datetime.now(UTC)])
    totals = iter([4000, 4242])

    async def fake_refresh_printer_counters(printer, defaults):
        printer.page_count_total = next(totals)
        printer.page_count_copy = None
        printer.page_count_print = None
        printer.page_count_confidence = "unsupported"
        printer.page_count_checked_at = next(simulated_times)
        return True

    monkeypatch.setattr(printers_router, "refresh_printer_counters", fake_refresh_printer_counters)
    client.post(f"/api/v1/printers/{printer_id}/check-counters", headers=auth_headers)
    client.post(f"/api/v1/printers/{printer_id}/check-counters", headers=auth_headers)

    history = client.get(f"/api/v1/printers/{printer_id}/counter-history", headers=auth_headers)
    assert history.status_code == 200
    # Two readings a day apart, both inside the default 30-day window —
    # two buckets. Yesterday's has no earlier reading to diff against
    # (null); today's reflects today's reading minus yesterday's — proving
    # check-counters actually persisted both readings, not just returned
    # the latest values.
    body = {row["bucket_start"]: row for row in history.json()}
    assert len(body) == 2
    yesterday, today = sorted(body.keys())
    assert body[yesterday]["total_delta"] is None
    assert body[today]["total_delta"] == 242


def test_check_counters_does_not_record_a_reading_on_failure(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    async def fake_refresh_printer_counters(printer, defaults):
        return False

    monkeypatch.setattr(printers_router, "refresh_printer_counters", fake_refresh_printer_counters)
    response = client.post(f"/api/v1/printers/{printer_id}/check-counters", headers=auth_headers)
    assert response.status_code == 200

    history = client.get(f"/api/v1/printers/{printer_id}/counter-history", headers=auth_headers)
    assert history.json() == []


def test_counter_history_defaults_to_30_days(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "SNMP Printer", "ip_address": "10.0.0.30"},
    )
    printer_id = create.json()["id"]

    response = client.get(f"/api/v1/printers/{printer_id}/counter-history", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_counter_history_requires_some_auth(client, mock_failed_probe):
    url = "/api/v1/printers/00000000-0000-0000-0000-000000000000/counter-history"
    response = client.get(url)
    assert response.status_code == 401


def test_archive_printer_removes_queue_sets_archived_at_and_hides_from_default_list(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    remove_calls = []
    monkeypatch.setattr(
        printers_router, "remove_queue", lambda printer_id: remove_calls.append(printer_id)
    )

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Retiring Printer", "ip_address": "10.0.0.16"},
    )
    printer_id = create.json()["id"]

    response = client.post(f"/api/v1/printers/{printer_id}/archive", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["archived_at"] is not None
    assert remove_calls == [printer_id]

    default_list = client.get("/api/v1/printers", headers=auth_headers).json()
    assert printer_id not in [p["id"] for p in default_list]

    full_list = client.get(
        "/api/v1/printers", params={"include_archived": True}, headers=auth_headers
    ).json()
    assert printer_id in [p["id"] for p in full_list]

    # Still directly reachable — archiving must never delete the row or its
    # (in this case nonexistent, but the point is the row survives) history.
    detail = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers)
    assert detail.status_code == 200


def test_unarchive_printer_resyncs_queue_and_clears_archived_at(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    sync_calls = []
    monkeypatch.setattr(printers_router, "remove_queue", lambda printer_id, is_virtual=False: None)
    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: sync_calls.append(printer_id),
    )

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Coming Back", "ip_address": "10.0.0.17"},
    )
    printer_id = create.json()["id"]
    client.post(f"/api/v1/printers/{printer_id}/archive", headers=auth_headers)

    response = client.post(f"/api/v1/printers/{printer_id}/unarchive", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["archived_at"] is None
    assert printer_id in sync_calls

    default_list = client.get("/api/v1/printers", headers=auth_headers).json()
    assert printer_id in [p["id"] for p in default_list]


def test_archive_printer_requires_admin(client, auth_headers, viewer_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Protected", "ip_address": "10.0.0.18"},
    )
    printer_id = create.json()["id"]

    response = client.post(f"/api/v1/printers/{printer_id}/archive", headers=viewer_headers)
    assert response.status_code == 403


def test_detect_toner_cartridges_creates_and_updates_rows(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Cartridge Printer", "ip_address": "10.0.0.31"},
    )
    printer_id = create.json()["id"]

    # An existing black cartridge with an admin-entered cost — detect must
    # not touch cost/yield_pages, only the detected_* fields.
    client.put(
        f"/api/v1/printers/{printer_id}/toner-cartridges",
        headers=auth_headers,
        json=[{"color": "black", "cost": 55.0, "yield_pages": 3000}],
    )

    def fake_get_toner_supplies(ip, config):
        return [
            DetectedSupply(
                description="410X High Yield Black",
                color="black",
                high_capacity=True,
                level_percent=62,
            ),
            DetectedSupply(
                description="CRG-069 Cyan", color="cyan", high_capacity=None, level_percent=None
            ),
        ]

    monkeypatch.setattr(snmp_counters_module, "get_toner_supplies", fake_get_toner_supplies)

    response = client.post(
        f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unmatched"] == []

    by_color = {c["color"]: c for c in body["cartridges"]}
    assert by_color["black"]["cost"] == 55.0  # untouched
    assert by_color["black"]["detected_description"] == "410X High Yield Black"
    assert by_color["black"]["detected_high_capacity"] is True
    assert by_color["black"]["detected_at"] is not None
    assert by_color["black"]["current_level_percent"] == 62
    assert by_color["black"]["level_checked_at"] is not None

    # cyan didn't exist before detect — created with a zero placeholder
    # cost/yield, still safe per compute_printer_rate's "not configured"
    # fallback rule.
    assert by_color["cyan"]["cost"] == 0.0
    assert by_color["cyan"]["yield_pages"] == 0
    assert by_color["cyan"]["detected_description"] == "CRG-069 Cyan"
    assert by_color["cyan"]["detected_high_capacity"] is None
    assert by_color["cyan"]["current_level_percent"] is None


async def test_detect_toner_cartridges_records_history_only_for_known_levels(
    client, auth_headers, mock_failed_probe, monkeypatch, db_session_factory
):
    """sync_toner_levels should append a PrinterTonerReading for black
    (level_percent=62, known) but not cyan (level_percent=None, unknown)
    — an all-unknown history point would just be chart noise."""
    from sqlalchemy import select

    from app.models.report import PrinterTonerReading

    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "History Printer", "ip_address": "10.0.0.38"},
    )
    printer_id = create.json()["id"]

    def fake_get_toner_supplies(ip, config):
        return [
            DetectedSupply(
                description="Black Toner", color="black", high_capacity=None, level_percent=62
            ),
            DetectedSupply(
                description="Cyan Toner", color="cyan", high_capacity=None, level_percent=None
            ),
        ]

    monkeypatch.setattr(snmp_counters_module, "get_toner_supplies", fake_get_toner_supplies)

    response = client.post(
        f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=auth_headers
    )
    assert response.status_code == 200

    async with db_session_factory() as db:
        result = await db.execute(
            select(PrinterTonerReading).where(
                PrinterTonerReading.printer_id == uuid.UUID(printer_id)
            )
        )
        readings = result.scalars().all()

    assert len(readings) == 1
    assert readings[0].color == "black"
    assert readings[0].level_percent == 62


def test_detect_toner_cartridges_reports_unmatched_supplies(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Weird Supplies Printer", "ip_address": "10.0.0.32"},
    )
    printer_id = create.json()["id"]

    def fake_get_toner_supplies(ip, config):
        return [
            DetectedSupply(
                description="Unknown Part 4471", color=None, high_capacity=None, level_percent=30
            )
        ]

    monkeypatch.setattr(snmp_counters_module, "get_toner_supplies", fake_get_toner_supplies)

    response = client.post(
        f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cartridges"] == []
    assert body["unmatched"] == [
        {
            "description": "Unknown Part 4471",
            "color": None,
            "high_capacity": None,
            "level_percent": 30,
        }
    ]


def test_detect_toner_cartridges_snmp_failure_returns_502(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Unreachable Printer", "ip_address": "10.0.0.33"},
    )
    printer_id = create.json()["id"]

    def fake_get_toner_supplies(ip, config):
        raise SnmpProbeError("No response from device.")

    monkeypatch.setattr(snmp_counters_module, "get_toner_supplies", fake_get_toner_supplies)

    response = client.post(
        f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=auth_headers
    )
    assert response.status_code == 502


def test_detect_toner_cartridges_requires_admin(
    client, auth_headers, viewer_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Protected Cartridge Printer", "ip_address": "10.0.0.34"},
    )
    printer_id = create.json()["id"]

    response = client.post(
        f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=viewer_headers
    )
    assert response.status_code == 403


def test_update_toner_cartridges_preserves_detected_fields_across_put(
    client, auth_headers, mock_failed_probe, monkeypatch
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Repriced Printer", "ip_address": "10.0.0.35"},
    )
    printer_id = create.json()["id"]

    def fake_get_toner_supplies(ip, config):
        return [
            DetectedSupply(
                description="056H Black", color="black", high_capacity=None, level_percent=80
            )
        ]

    monkeypatch.setattr(snmp_counters_module, "get_toner_supplies", fake_get_toner_supplies)
    client.post(f"/api/v1/printers/{printer_id}/toner-cartridges/detect", headers=auth_headers)

    # Admin edits cost via the normal full-replace PUT — detected_description
    # must survive, not get wiped by the delete-and-recreate.
    response = client.put(
        f"/api/v1/printers/{printer_id}/toner-cartridges",
        headers=auth_headers,
        json=[{"color": "black", "cost": 62.5, "yield_pages": 3100}],
    )
    assert response.status_code == 200
    body = response.json()[0]
    assert body["cost"] == 62.5
    assert body["detected_description"] == "056H Black"
    # current_level_percent/level_checked_at are also SNMP-detected, same
    # carry-over rule as detected_description above.
    assert body["current_level_percent"] == 80
    assert body["level_checked_at"] is not None


def test_toner_cartridge_warning_threshold_defaults_and_is_settable(
    client, auth_headers, mock_failed_probe
):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Threshold Printer", "ip_address": "10.0.0.37"},
    )
    printer_id = create.json()["id"]

    default_response = client.put(
        f"/api/v1/printers/{printer_id}/toner-cartridges",
        headers=auth_headers,
        json=[{"color": "black", "cost": 60, "yield_pages": 3000}],
    )
    assert default_response.json()[0]["warning_threshold_percent"] == 15

    custom_response = client.put(
        f"/api/v1/printers/{printer_id}/toner-cartridges",
        headers=auth_headers,
        json=[
            {"color": "black", "cost": 60, "yield_pages": 3000, "warning_threshold_percent": 25}
        ],
    )
    assert custom_response.json()[0]["warning_threshold_percent"] == 25


def test_toner_cartridge_model_is_per_color(client, auth_headers, mock_failed_probe):
    create = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Color Printer", "ip_address": "10.0.0.36"},
    )
    printer_id = create.json()["id"]

    response = client.put(
        f"/api/v1/printers/{printer_id}/toner-cartridges",
        headers=auth_headers,
        json=[
            {"color": "black", "cost": 60, "yield_pages": 3000, "model": "TN-227BK"},
            {"color": "cyan", "cost": 70, "yield_pages": 2300, "model": "TN-227C"},
        ],
    )
    assert response.status_code == 200
    by_color = {c["color"]: c for c in response.json()}
    assert by_color["black"]["model"] == "TN-227BK"
    assert by_color["cyan"]["model"] == "TN-227C"

    listing = client.get(f"/api/v1/printers/{printer_id}/toner-cartridges", headers=auth_headers)
    by_color = {c["color"]: c for c in listing.json()}
    assert by_color["black"]["model"] == "TN-227BK"
    assert by_color["cyan"]["model"] == "TN-227C"


def test_create_virtual_queue_defaults(client, auth_headers):
    response = client.post(
        "/api/v1/printers/virtual",
        headers=auth_headers,
        json={"name": "Follow-Me Printing", "building": "Main"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["is_virtual"] is True
    assert body["ip_address"] is None
    assert body["follow_me_enabled"] is True
    assert body["release_required"] is False
    assert body["release_token"] is not None
    assert body["airprint_enabled"] is True
    assert body["snmp_enabled"] is False
    assert body["building"] == "Main"


def test_create_virtual_queue_requires_admin(client):
    response = client.post("/api/v1/printers/virtual", json={"name": "Follow-Me Printing"})
    assert response.status_code == 401


def test_update_virtual_queue_cannot_disable_follow_me(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"follow_me_enabled": False},
    )
    assert response.status_code == 400


def test_update_virtual_queue_cannot_enable_release_required(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"release_required": True},
    )
    assert response.status_code == 400


def test_update_virtual_queue_allows_unrelated_fields(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.patch(
        f"/api/v1/printers/{printer_id}",
        headers=auth_headers,
        json={"department": "IT"},
    )
    assert response.status_code == 200
    assert response.json()["department"] == "IT"


def test_check_status_rejects_virtual_printer(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.post(f"/api/v1/printers/{printer_id}/check-status", headers=auth_headers)
    assert response.status_code == 400


def test_check_counters_rejects_virtual_printer(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.post(f"/api/v1/printers/{printer_id}/check-counters", headers=auth_headers)
    assert response.status_code == 400


def test_discover_rejects_virtual_printer(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.post(f"/api/v1/printers/{printer_id}/discover", headers=auth_headers)
    assert response.status_code == 400


def test_release_bypass_rejects_virtual_printer(client, auth_headers):
    create = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    printer_id = create.json()["id"]
    response = client.post(
        f"/api/v1/printers/{printer_id}/release-bypasses",
        headers=auth_headers,
        json={"user_email": "someone@example.org"},
    )
    assert response.status_code == 400


def test_create_virtual_queue_does_not_call_capability_discovery(
    client, auth_headers, monkeypatch
):
    called = False

    async def fake_refresh(printer):
        nonlocal called
        called = True

    monkeypatch.setattr(printers_router, "refresh_printer_capabilities", fake_refresh)
    response = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    assert response.status_code == 201
    assert called is False


def test_create_virtual_queue_skips_release_queue_sync(client, auth_headers, monkeypatch):
    sync_calls = []
    monkeypatch.setattr(
        printers_router,
        "sync_queue",
        lambda printer_id, is_virtual=False: sync_calls.append(is_virtual),
    )
    response = client.post(
        "/api/v1/printers/virtual", headers=auth_headers, json={"name": "Follow-Me Printing"}
    )
    assert response.status_code == 201
    assert sync_calls == [True]


def test_tls_supported_detected_when_advertised(client, auth_headers, monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        return ProbeResult(
            raw_attributes={
                "printer-make-and-model": "Mock MFP 3000",
                "uri-security-supported": ["none", "tls"],
            },
            resolved_path=ipp_path or "/ipp/print",
        )

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer)
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "TLS-Capable Printer", "ip_address": "10.0.0.40"},
    )
    assert response.status_code == 201
    assert response.json()["capabilities"]["tls_supported"] is True


def test_tls_supported_false_when_not_advertised(client, auth_headers, mock_successful_probe):
    # mock_successful_probe's raw_attributes has no uri-security-supported key at all.
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "No TLS Signal Printer", "ip_address": "10.0.0.41"},
    )
    assert response.status_code == 201
    assert response.json()["capabilities"]["tls_supported"] is False


def test_tls_supported_false_when_only_none_advertised(client, auth_headers, monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        return ProbeResult(
            raw_attributes={
                "printer-make-and-model": "Mock MFP 3000",
                "uri-security-supported": ["none"],
            },
            resolved_path=ipp_path or "/ipp/print",
        )

    monkeypatch.setattr(printer_discovery, "probe_printer", fake_probe_printer)
    response = client.post(
        "/api/v1/printers",
        headers=auth_headers,
        json={"name": "Plain IPP Only Printer", "ip_address": "10.0.0.42"},
    )
    assert response.status_code == 201
    assert response.json()["capabilities"]["tls_supported"] is False
