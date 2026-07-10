import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer


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
        printer = Printer(
            name="Zabbix Test Printer",
            ip_address="10.0.0.50",
            building="Main",
            room="101",
            department="IT",
            status="online",
            page_count_total=1234,
            page_count_confidence="verified",
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


@pytest_asyncio.fixture
async def archived_printer_id(db_session_factory):
    from datetime import UTC, datetime

    async with db_session_factory() as session:
        printer = Printer(
            name="Archived Printer", ip_address="10.0.0.51", archived_at=datetime.now(UTC)
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def zabbix_token(client, auth_headers):
    response = client.put(
        "/api/v1/settings/zabbix", headers=auth_headers, json={"enabled": True}
    )
    assert response.status_code == 200
    return response.json()["api_token"]


@pytest.fixture
def zabbix_headers(zabbix_token):
    return {"X-Zabbix-Token": zabbix_token}


def test_enabling_zabbix_generates_a_token(client, auth_headers):
    response = client.put(
        "/api/v1/settings/zabbix", headers=auth_headers, json={"enabled": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["api_token"]


def test_regenerate_token_changes_it_and_invalidates_the_old_one(
    client, auth_headers, zabbix_token
):
    old_headers = {"X-Zabbix-Token": zabbix_token}
    response = client.post("/api/v1/settings/zabbix/regenerate-token", headers=auth_headers)
    assert response.status_code == 200
    new_token = response.json()["api_token"]
    assert new_token != zabbix_token

    assert client.get("/api/v1/integrations/zabbix/summary", headers=old_headers).status_code == 401
    assert (
        client.get(
            "/api/v1/integrations/zabbix/summary", headers={"X-Zabbix-Token": new_token}
        ).status_code
        == 200
    )


def test_zabbix_endpoint_rejects_missing_token(client):
    # FastAPI's own APIKeyHeader protection (auto_error) fires before our
    # code even runs when the header is absent entirely — confirmed same
    # 401 behavior as the existing X-Backend-Token dependency.
    response = client.get("/api/v1/integrations/zabbix/summary")
    assert response.status_code == 401


def test_zabbix_endpoint_rejects_wrong_token(client, zabbix_token):
    response = client.get(
        "/api/v1/integrations/zabbix/summary", headers={"X-Zabbix-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_zabbix_endpoint_rejects_when_disabled(client, auth_headers, zabbix_token):
    client.put("/api/v1/settings/zabbix", headers=auth_headers, json={"enabled": False})
    response = client.get(
        "/api/v1/integrations/zabbix/summary", headers={"X-Zabbix-Token": zabbix_token}
    )
    assert response.status_code == 401


def test_zabbix_summary_shape(client, zabbix_headers):
    response = client.get("/api/v1/integrations/zabbix/summary", headers=zabbix_headers)
    assert response.status_code == 200
    body = response.json()
    for field in (
        "total_jobs",
        "forwarded_jobs",
        "failed_jobs",
        "cancelled_jobs",
        "total_pages",
        "color_pages",
        "mono_pages",
        "duplex_pages",
        "simplex_pages",
    ):
        assert field in body
        assert isinstance(body[field], int)


def test_zabbix_printer_discovery_shape(client, zabbix_headers, printer_id):
    response = client.get("/api/v1/integrations/zabbix/printers", headers=zabbix_headers)
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    entries = body["data"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["{#PRINTER_ID}"] == printer_id
    assert entry["{#PRINTER_NAME}"] == "Zabbix Test Printer"
    assert entry["{#PRINTER_IP}"] == "10.0.0.50"
    assert entry["{#PRINTER_BUILDING}"] == "Main"
    assert entry["{#PRINTER_ROOM}"] == "101"
    assert entry["{#PRINTER_DEPARTMENT}"] == "IT"


def test_zabbix_printer_discovery_excludes_archived(client, zabbix_headers, archived_printer_id):
    response = client.get("/api/v1/integrations/zabbix/printers", headers=zabbix_headers)
    assert response.status_code == 200
    ids = [entry["{#PRINTER_ID}"] for entry in response.json()["data"]]
    assert archived_printer_id not in ids


def test_zabbix_printer_detail(client, zabbix_headers, printer_id):
    response = client.get(
        f"/api/v1/integrations/zabbix/printers/{printer_id}", headers=zabbix_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "online"
    assert body["queue_sync_error"] == ""
    assert body["page_count_total"] == 1234
    assert body["page_count_confidence"] == "verified"
    assert body["building"] == "Main"


def test_zabbix_printer_detail_404s_for_missing(client, zabbix_headers):
    response = client.get(
        "/api/v1/integrations/zabbix/printers/00000000-0000-0000-0000-000000000000",
        headers=zabbix_headers,
    )
    assert response.status_code == 404


def test_zabbix_printer_detail_404s_for_archived(client, zabbix_headers, archived_printer_id):
    response = client.get(
        f"/api/v1/integrations/zabbix/printers/{archived_printer_id}", headers=zabbix_headers
    )
    assert response.status_code == 404


def test_download_template_requires_admin(client, zabbix_headers):
    # The Zabbix data token must not work for the config/download endpoints
    # — those are a completely different trust boundary (admin JWT only).
    response = client.get("/api/v1/settings/zabbix/template", headers=zabbix_headers)
    assert response.status_code == 401


def test_download_template(client, auth_headers, zabbix_token):
    response = client.get("/api/v1/settings/zabbix/template", headers=auth_headers)
    assert response.status_code == 200
    assert b"printops.summary.raw" in response.content
