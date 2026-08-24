"""A copier is a capability of a printer, not a separate thing.

Every copier in this district is also a printer, and keeping them as two
records meant one machine appeared in two places with its connection, model,
location and meter described in both. The printer row is the machine now, and
walk-up copy tracking is something it can be given.

What these mostly guard is the turning-off case. Every foreign key into
mfp_devices is ON DELETE CASCADE, and the accounting rides on that row: 1620
provisioned accounts, 1735 counter readings and every usage record on this
server hang off mfp_device_id. Turning a feature off in the UI must never be a
way to destroy that.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
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
    yield async_sessionmaker(engine, expire_on_commit=False)
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


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(
            id=uuid.uuid4(),
            name="ES Veronica Copier",
            ip_address="10.10.3.36",
            manufacturer="Konica",
            model="bizhub 950i",
            building="ES",
            room="Office",
        )
        session.add(printer)
        await session.commit()
        return str(printer.id)


async def _device_for(db_session_factory, printer_id):
    async with db_session_factory() as session:
        return (
            await session.execute(
                select(MfpDevice).where(MfpDevice.printer_id == uuid.UUID(printer_id))
            )
        ).scalar_one_or_none()


async def test_a_printer_is_not_a_copier_until_it_is_told_it_is(
    client, auth_headers, printer_id, db_session_factory
):
    printer = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers).json()
    assert printer["copier_enabled"] is False

    copier = client.get(f"/api/v1/printers/{printer_id}/copier", headers=auth_headers)
    assert copier.status_code == 200
    assert copier.json() is None, "not a failure — an ordinary answer"
    assert await _device_for(db_session_factory, printer_id) is None


async def test_enabling_seeds_the_copier_from_what_the_printer_already_knows(
    client, auth_headers, printer_id, db_session_factory
):
    """Nobody retypes an address and a location that are already on screen."""
    response = client.post(f"/api/v1/printers/{printer_id}/copier/enable", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "ES Veronica Copier"
    assert body["ip_address"] == "10.10.3.36"
    assert body["building"] == "ES"
    assert body["room"] == "Office"
    assert body["model"] == "bizhub 950i"

    printer = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers).json()
    assert printer["copier_enabled"] is True


async def test_turning_it_off_keeps_the_copier_and_everything_on_it(
    client, auth_headers, printer_id, db_session_factory
):
    """The one that matters. Every FK into mfp_devices cascades, so deleting
    the row on disable would take the district's copy accounting with it."""
    client.post(f"/api/v1/printers/{printer_id}/copier/enable", headers=auth_headers)
    device = await _device_for(db_session_factory, printer_id)
    async with db_session_factory() as session:
        session.add(
            CopierUsageRecord(
                mfp_device_id=device.id,
                vendor="konica",
                staff_email="ateacher@district.org",
                external_identity_used="12345",
                source_connector="konica_bizhub",
                page_count=40,
                raw_payload={},
            )
        )
        await session.commit()

    response = client.post(f"/api/v1/printers/{printer_id}/copier/disable", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["copier_enabled"] is False
    still_there = await _device_for(db_session_factory, printer_id)
    assert still_there is not None, "the device row must survive"
    async with db_session_factory() as session:
        rows = (await session.execute(select(CopierUsageRecord))).scalars().all()
    assert len(rows) == 1, "and so must its history"


async def test_turning_it_back_on_reuses_the_same_copier(
    client, auth_headers, printer_id, db_session_factory
):
    """Re-enabling must not create a second device — the accounting is attached
    to the first one."""
    first = client.post(f"/api/v1/printers/{printer_id}/copier/enable", headers=auth_headers).json()
    client.post(f"/api/v1/printers/{printer_id}/copier/disable", headers=auth_headers)
    second = client.post(
        f"/api/v1/printers/{printer_id}/copier/enable", headers=auth_headers
    ).json()

    assert first["id"] == second["id"]
    async with db_session_factory() as session:
        devices = (await session.execute(select(MfpDevice))).scalars().all()
    assert len(devices) == 1


async def test_an_existing_copier_is_adopted_rather_than_duplicated(
    client, auth_headers, printer_id, db_session_factory
):
    """The upgrade case: copiers linked to a printer before this existed."""
    async with db_session_factory() as session:
        session.add(
            MfpDevice(
                id=uuid.uuid4(),
                printer_id=uuid.UUID(printer_id),
                name="Veronica (already set up)",
                vendor="konica",
                connector_type="konica_bizhub",
            )
        )
        await session.commit()

    body = client.post(f"/api/v1/printers/{printer_id}/copier/enable", headers=auth_headers).json()

    assert body["name"] == "Veronica (already set up)", "the admin's own setup, untouched"
    async with db_session_factory() as session:
        devices = (await session.execute(select(MfpDevice))).scalars().all()
    assert len(devices) == 1


async def test_only_an_admin_can_change_what_a_machine_is(client, printer_id):
    assert client.post(f"/api/v1/printers/{printer_id}/copier/enable").status_code in (401, 403)
    assert client.post(f"/api/v1/printers/{printer_id}/copier/disable").status_code in (401, 403)
