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
    client = MosyleClient("https://businessapi.mosyle.com/v1", "tok", "admin@x.com", "pw")

    async def fake_post(self, path, body):
        return {"not": "the expected shape"}

    monkeypatch.setattr(MosyleClient, "_post", fake_post)
    with pytest.raises(MosyleError, match="Unexpected response shape"):
        await client.list_devices()


@pytest.mark.asyncio
async def test_list_devices_parses_real_shape(monkeypatch):
    client = MosyleClient("https://businessapi.mosyle.com/v1", "tok", "admin@x.com", "pw")

    async def fake_post(self, path, body):
        assert path == "/listdevices"
        return [{"devices": [{"serial_number": "SN1", "wifi_mac_address": "aa:bb:cc:dd:ee:ff"}]}]

    monkeypatch.setattr(MosyleClient, "_post", fake_post)
    devices = await client.list_devices()
    assert devices == [{"serial_number": "SN1", "wifi_mac_address": "aa:bb:cc:dd:ee:ff"}]


@pytest.mark.asyncio
async def test_sync_devices_requires_enabled_settings(db_session_factory):
    async with db_session_factory() as db:
        with pytest.raises(MosyleError, match="not configured/enabled"):
            await sync_devices(db)


@pytest.mark.asyncio
async def test_sync_devices_populates_cache_and_clears_error(db_session_factory, monkeypatch):
    async def fake_list_devices(self, os="mac"):
        return [
            {"wifi_mac_address": "aa:bb:cc:dd:ee:ff", "serial_number": "SN1", "userid": "42"},
            {"wifi_mac_address": None, "serial_number": "SN2", "userid": "43"},  # no MAC, skipped
        ]

    async def fake_list_users(self):
        return {"42": {"email": "jdoe@example.com", "name": "Jane Doe"}}

    monkeypatch.setattr(MosyleClient, "list_devices", fake_list_devices)
    monkeypatch.setattr(MosyleClient, "list_users", fake_list_users)

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
