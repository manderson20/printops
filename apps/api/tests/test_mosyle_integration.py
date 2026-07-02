import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt
from app.integrations.mosyle import MosyleClient, MosyleError, normalize_mac, run_sync, sync_devices
from app.models.base import Base
from app.models.mosyle import MosyleDevice, MosyleSettings


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def test_normalize_mac():
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac(" AA:BB:CC:DD:EE:FF ") == "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_list_devices_unexpected_shape_raises_mosyle_error(monkeypatch):
    client = MosyleClient("https://managerapi.mosyle.com/v2", "tok", "admin@x.com", "pw")

    async def fake_post(self, http_client, path, body):
        return {"not": "the expected shape"}

    monkeypatch.setattr(MosyleClient, "_post", fake_post)
    with pytest.raises(MosyleError, match="Unexpected response shape"):
        await client.list_devices()


@pytest.mark.asyncio
async def test_list_devices_parses_real_shape_single_page(monkeypatch):
    client = MosyleClient("https://managerapi.mosyle.com/v2", "tok", "admin@x.com", "pw")
    calls = []

    async def fake_post(self, http_client, path, body):
        calls.append((path, body))
        return {"devices": [{"serial_number": "SN1", "wifi_mac_address": "aa:bb:cc:dd:ee:ff"}], "rows": 1}

    monkeypatch.setattr(MosyleClient, "_post", fake_post)
    devices = await client.list_devices()
    assert devices == [{"serial_number": "SN1", "wifi_mac_address": "aa:bb:cc:dd:ee:ff"}]
    assert calls == [("listdevices", {"options": {"os": "mac", "page": 0}})]


@pytest.mark.asyncio
async def test_list_devices_follows_pagination(monkeypatch):
    client = MosyleClient("https://managerapi.mosyle.com/v2", "tok", "admin@x.com", "pw")
    pages = [
        {"devices": [{"serial_number": "SN1"}], "rows": 2},
        {"devices": [{"serial_number": "SN2"}], "rows": 2},
    ]

    async def fake_post(self, http_client, path, body):
        return pages[body["options"]["page"]]

    monkeypatch.setattr(MosyleClient, "_post", fake_post)
    devices = await client.list_devices()
    assert [d["serial_number"] for d in devices] == ["SN1", "SN2"]


@pytest.mark.asyncio
async def test_login_reads_bearer_from_response_header(monkeypatch):
    import httpx

    client = MosyleClient("https://managerapi.mosyle.com/v2", "tok", "admin@x.com", "pw")

    class FakeResponse:
        status_code = 200
        headers = {"Authorization": "Bearer abc123"}
        text = ""

    async def fake_post(self, url, headers=None, json=None):
        assert json == {"accessToken": "tok", "email": "admin@x.com", "password": "pw"}
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    async with httpx.AsyncClient() as http_client:
        bearer = await client._login(http_client)
    assert bearer == "Bearer abc123"


@pytest.mark.asyncio
async def test_sync_devices_requires_enabled_settings(db_session_factory):
    async with db_session_factory() as db:
        with pytest.raises(MosyleError, match="not configured/enabled"):
            await sync_devices(db)


@pytest.mark.asyncio
async def test_sync_devices_populates_cache_from_embedded_user_fields(db_session_factory, monkeypatch):
    async def fake_list_devices(self, os="mac"):
        return [
            {
                "wifi_mac_address": "aa:bb:cc:dd:ee:ff",
                "serial_number": "SN1",
                "useremail": "jdoe@example.com",
                "username": "Jane Doe",
            },
            {"wifi_mac_address": None, "serial_number": "SN2"},  # no MAC, skipped
        ]

    monkeypatch.setattr(MosyleClient, "list_devices", fake_list_devices)

    async with db_session_factory() as db:
        db.add(
            MosyleSettings(
                enabled=True,
                access_token_encrypted=encrypt("tok"),
                admin_email="admin@x.com",
                admin_password_encrypted=encrypt("pw"),
                last_sync_error="stale error from before",
            )
        )
        await db.commit()

        count = await sync_devices(db)
        assert count == 1

        cached = (await db.execute(select(MosyleDevice))).scalars().all()
        assert len(cached) == 1
        assert cached[0].mac_address == "AA:BB:CC:DD:EE:FF"
        assert cached[0].user_email == "jdoe@example.com"

        settings = (await db.execute(select(MosyleSettings))).scalar_one()
        assert settings.last_sync_error is None
        assert settings.device_count == 1
        assert settings.last_synced_at is not None


@pytest.mark.asyncio
async def test_sync_devices_drops_ambiguous_shared_macs(db_session_factory, monkeypatch):
    """Two different devices reporting the same MAC (confirmed against a
    real Mosyle tenant — devices without WiFi fall back to a shared
    Bluetooth identifier) must not crash the sync (unique constraint) or
    get cached under either person -- resolving a shared MAC to one of two
    possible users risks attributing a job to the wrong one."""

    async def fake_list_devices(self, os="mac"):
        return [
            {"wifi_mac_address": "aa:bb:cc:dd:ee:ff", "serial_number": "SN1", "useremail": "a@x.com"},
            {"wifi_mac_address": "aa:bb:cc:dd:ee:ff", "serial_number": "SN2", "useremail": "b@x.com"},
            {"wifi_mac_address": "11:22:33:44:55:66", "serial_number": "SN3", "useremail": "c@x.com"},
        ]

    monkeypatch.setattr(MosyleClient, "list_devices", fake_list_devices)

    async with db_session_factory() as db:
        db.add(
            MosyleSettings(
                enabled=True,
                access_token_encrypted=encrypt("tok"),
                admin_email="admin@x.com",
                admin_password_encrypted=encrypt("pw"),
            )
        )
        await db.commit()

        count = await sync_devices(db)
        assert count == 1  # only the unambiguous device

        cached = (await db.execute(select(MosyleDevice))).scalars().all()
        assert [d.mac_address for d in cached] == ["11:22:33:44:55:66"]
        assert cached[0].user_email == "c@x.com"


@pytest.mark.asyncio
async def test_run_sync_records_failure_on_settings(db_session_factory, monkeypatch):
    async def fake_list_devices(self, os="mac"):
        raise MosyleError("simulated API outage")

    monkeypatch.setattr(MosyleClient, "list_devices", fake_list_devices)

    async with db_session_factory() as db:
        db.add(
            MosyleSettings(
                enabled=True,
                access_token_encrypted=encrypt("tok"),
                admin_email="admin@x.com",
                admin_password_encrypted=encrypt("pw"),
            )
        )
        await db.commit()

        with pytest.raises(MosyleError):
            await run_sync(db)

        settings = (await db.execute(select(MosyleSettings))).scalar_one()
        assert settings.last_sync_error == "simulated API outage"
