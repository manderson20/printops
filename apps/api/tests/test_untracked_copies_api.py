from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.models.snmp import PrinterCounterReading
from app.models.untracked_copies import UntrackedCopySettings


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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


async def _make_printer(db_session_factory, confidence, name="MFP"):
    async with db_session_factory() as session:
        printer = Printer(
            name=name,
            ip_address="10.0.0.9",
            snmp_enabled=True,
            page_count_confidence=confidence,
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer.id


async def _make_reading(db_session_factory, printer_id, recorded_at, total, copy=None, print_=None):
    async with db_session_factory() as session:
        session.add(
            PrinterCounterReading(
                printer_id=printer_id,
                recorded_at=recorded_at,
                page_count_total=total,
                page_count_copy=copy,
                page_count_print=print_,
            )
        )
        await session.commit()


async def _link_mfp_device(db_session_factory, printer_id):
    async with db_session_factory() as session:
        session.add(MfpDevice(name="Linked MFP", printer_id=printer_id))
        await session.commit()


def _enable(client, admin_headers):
    response = client.put(
        "/api/v1/settings/untracked-copies", headers=admin_headers, json={"enabled": True}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _enable_since(db_session_factory, enabled_at):
    """Directly stamps enabled_at in the past, rather than going through
    the API (which always stamps "now") — lets delta-computation tests
    place readings/jobs at realistic, controllable timestamps near real
    "now" (so a print job's real-"now" created_at naturally falls on the
    same calendar day as a reading placed at "now" too) while still being
    safely after enabled_at."""
    async with db_session_factory() as session:
        session.add(UntrackedCopySettings(enabled=True, enabled_at=enabled_at))
        await session.commit()


def _make_job(client, printer_id, backend_headers, page_count, created_at=None):
    create = client.post(
        "/api/v1/jobs",
        json={"printer_id": str(printer_id), "submitted_by": "someone@example.org"},
        headers=backend_headers,
    )
    job_id = create.json()["id"]
    client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": "forwarded", "page_count": page_count},
        headers=backend_headers,
    )
    return job_id


def test_settings_default_disabled(client, admin_headers):
    response = client.get("/api/v1/settings/untracked-copies", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["enabled_at"] is None


def test_enabling_stamps_and_re_stamps_enabled_at(client, admin_headers):
    first = _enable(client, admin_headers)
    assert first["enabled_at"] is not None

    disabled = client.put(
        "/api/v1/settings/untracked-copies", headers=admin_headers, json={"enabled": False}
    )
    assert disabled.json()["enabled_at"] == first["enabled_at"]  # untouched while off

    second = _enable(client, admin_headers)
    # A fresh re-enable is treated as a fresh start, not a resume.
    assert second["enabled_at"] != first["enabled_at"]


def test_settings_write_requires_admin(client):
    response = client.put("/api/v1/settings/untracked-copies", json={"enabled": True})
    assert response.status_code == 401


async def test_summary_disabled_returns_zeros(client, admin_headers, db_session_factory):
    printer_id = await _make_printer(db_session_factory, "verified")
    now = datetime.now(UTC)
    await _make_reading(db_session_factory, printer_id, now - timedelta(days=1), 1000, 400, 600)
    await _make_reading(db_session_factory, printer_id, now, 1100, 450, 650)

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    body = response.json()
    assert body["measured_copies"] == 0
    assert body["estimated_untracked"] == 0
    assert body["tracking_since"] is None


async def test_summary_measured_copies_from_verified_printer(
    client, admin_headers, db_session_factory
):
    # enabled_at set safely in the past so both readings (near real "now")
    # sit comfortably inside the tracked window without racing the clock.
    await _enable_since(db_session_factory, datetime.now(UTC) - timedelta(days=10))

    printer_id = await _make_printer(db_session_factory, "verified")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await _make_reading(db_session_factory, printer_id, yesterday, 1000, 400, 600)
    await _make_reading(db_session_factory, printer_id, now, 1050, 430, 620)

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    body = response.json()
    assert body["measured_copies"] == 30  # 430 - 400, direct from copy_delta
    assert body["estimated_untracked"] == 0
    assert body["printers"] == [
        {
            "printer_id": str(printer_id),
            "printer_name": "MFP",
            "measured_copies": 30,
            "estimated_untracked": 0,
        }
    ]


async def test_summary_lists_each_contributing_printer_sorted_by_total(
    client, admin_headers, db_session_factory
):
    await _enable_since(db_session_factory, datetime.now(UTC) - timedelta(days=10))
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    small = await _make_printer(db_session_factory, "verified", name="Small Copier")
    await _make_reading(db_session_factory, small, yesterday, 1000, 400, 600)
    await _make_reading(db_session_factory, small, now, 1010, 405, 605)  # +5

    big = await _make_printer(db_session_factory, "verified", name="Big Copier")
    await _make_reading(db_session_factory, big, yesterday, 2000, 800, 1200)
    await _make_reading(db_session_factory, big, now, 2100, 850, 1250)  # +50

    # A printer with no confidence-classified SNMP data at all contributes
    # nothing and must not show up as a noisy "0" row.
    await _make_printer(db_session_factory, None, name="No SNMP Data")

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    body = response.json()
    assert body["measured_copies"] == 55  # 5 + 50
    names_in_order = [p["printer_name"] for p in body["printers"]]
    assert names_in_order == ["Big Copier", "Small Copier"]  # sorted, largest first
    assert len(body["printers"]) == 2


async def test_summary_estimated_from_unsupported_printer(
    client, admin_headers, backend_headers, db_session_factory
):
    await _enable_since(db_session_factory, datetime.now(UTC) - timedelta(days=10))

    printer_id = await _make_printer(db_session_factory, "unsupported")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await _make_reading(db_session_factory, printer_id, yesterday, 1000)
    await _make_reading(db_session_factory, printer_id, now, 1100)  # total delta = 100
    # Job's created_at is real "now" (the create endpoint doesn't take an
    # explicit timestamp), same calendar day as the `now` reading above.
    _make_job(client, printer_id, backend_headers, page_count=60)  # PrintOps printed 60 of those

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    body = response.json()
    assert body["measured_copies"] == 0
    assert body["estimated_untracked"] == 40  # 100 - 60


async def test_summary_estimated_floors_at_zero(
    client, admin_headers, backend_headers, db_session_factory
):
    """If PrintOps' own recorded pages meet or exceed the counter delta
    (e.g. a job printed right at a day boundary), the estimate must never
    go negative."""
    await _enable_since(db_session_factory, datetime.now(UTC) - timedelta(days=10))

    printer_id = await _make_printer(db_session_factory, "unsupported")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await _make_reading(db_session_factory, printer_id, yesterday, 1000)
    await _make_reading(db_session_factory, printer_id, now, 1050)  # total delta = 50
    _make_job(client, printer_id, backend_headers, page_count=90)  # more than the delta

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    assert response.json()["estimated_untracked"] == 0


async def test_summary_excludes_printer_linked_to_mfp_device(
    client, admin_headers, db_session_factory
):
    await _enable_since(db_session_factory, datetime.now(UTC) - timedelta(days=10))

    printer_id = await _make_printer(db_session_factory, "verified")
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await _make_reading(db_session_factory, printer_id, yesterday, 1000, 400, 600)
    await _make_reading(db_session_factory, printer_id, now, 1050, 430, 620)
    await _link_mfp_device(db_session_factory, printer_id)

    response = client.get("/api/v1/reports/untracked-copies", headers=admin_headers)
    body = response.json()
    assert body["measured_copies"] == 0  # excluded -- already tracked via CopierUsageRecord


async def test_summary_ignores_readings_before_enabled_at(
    client, admin_headers, db_session_factory
):
    """Readings that predate the feature being turned on must never count
    as measured/estimated copies, even though the raw history exists."""
    settings = _enable(client, admin_headers)
    enabled_at = datetime.fromisoformat(settings["enabled_at"])

    printer_id = await _make_printer(db_session_factory, "verified")
    # Both readings are from before enabled_at.
    await _make_reading(
        db_session_factory, printer_id, enabled_at - timedelta(days=2), 1000, 400, 600
    )
    await _make_reading(
        db_session_factory, printer_id, enabled_at - timedelta(days=1), 1200, 500, 700
    )

    response = client.get(
        "/api/v1/reports/untracked-copies",
        headers=admin_headers,
        params={
            "start": (enabled_at - timedelta(days=3)).isoformat(),
            "end": (enabled_at - timedelta(hours=1)).isoformat(),
        },
    )
    body = response.json()
    assert body["measured_copies"] == 0
    assert body["estimated_untracked"] == 0
