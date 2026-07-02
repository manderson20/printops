import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.printers.ipp_client import PrinterProbeError, ProbeResult
from app.routers import printers as printers_router


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

    monkeypatch.setattr(printers_router, "probe_printer", fake_probe_printer)


@pytest.fixture
def mock_failed_probe(monkeypatch):
    async def fake_probe_printer(ip_address, port=631, tls=False, timeout=5, ipp_path=None):
        raise PrinterProbeError("Could not reach an IPP printer at 10.0.0.5:631: timed out")

    monkeypatch.setattr(printers_router, "probe_printer", fake_probe_printer)


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

    monkeypatch.setattr(printers_router, "probe_printer", fake_probe_printer_now_online)

    rediscovered = client.post(f"/api/v1/printers/{printer_id}/discover", headers=auth_headers)
    assert rediscovered.status_code == 200
    assert rediscovered.json()["capabilities_error"] is None
    assert rediscovered.json()["model"] == "Now Online"
