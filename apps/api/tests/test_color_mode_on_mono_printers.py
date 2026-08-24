"""A monochrome printer cannot have printed a colour page.

CUPS reports the colour mode a job *asked* for. A mono printer accepts a colour
job and prints it in grey without complaint, so the job arrives here recorded
as "color" — and Insights counts it as a colour page and bills it at the colour
rate. Several queues here offer colour on mono hardware (a printer too old for
driverless IPP falls back to a generic PPD that claims colour), so this is not
hypothetical: 18 such jobs existed on this server when this was written.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer
from app.printers.capabilities import recorded_color_mode


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
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


async def _printer(db_session_factory, capabilities):
    async with db_session_factory() as session:
        printer = Printer(
            id=uuid.uuid4(),
            name="ES 4th Grade Printer",
            ip_address="10.10.1.10",
            capabilities=capabilities,
        )
        session.add(printer)
        await session.commit()
        return str(printer.id)


def test_a_mono_printer_did_not_print_in_colour():
    assert recorded_color_mode({"color_supported": False}, "color") == "monochrome"


def test_a_colour_printer_is_believed():
    assert recorded_color_mode({"color_supported": True}, "color") == "color"


def test_an_unprobed_printer_is_left_alone():
    """The dangerous direction. A colour printer we simply failed to probe
    must not have its colour jobs rewritten to mono."""
    assert recorded_color_mode(None, "color") == "color"
    assert recorded_color_mode({}, "color") == "color"


def test_modes_that_are_not_colour_pass_through():
    assert recorded_color_mode({"color_supported": False}, "monochrome") == "monochrome"
    assert recorded_color_mode({"color_supported": False}, None) is None


async def test_update_job_records_grey_for_a_colour_job_on_a_mono_printer(
    client, backend_headers, db_session_factory
):
    printer_id = await _printer(db_session_factory, {"color_supported": False})
    created = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "cups_job_id": 7, "submitted_by": "sayers"},
        headers=backend_headers,
    )
    assert created.status_code == 201

    updated = client.patch(
        f"/api/v1/jobs/{created.json()['id']}",
        json={"status": "forwarded", "page_count": 3, "color_mode": "color"},
        headers=backend_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["color_mode"] == "monochrome"


async def test_update_job_keeps_colour_on_a_colour_printer(
    client, backend_headers, db_session_factory
):
    printer_id = await _printer(db_session_factory, {"color_supported": True})
    created = client.post(
        "/api/v1/jobs",
        json={"printer_id": printer_id, "cups_job_id": 8, "submitted_by": "sayers"},
        headers=backend_headers,
    )
    updated = client.patch(
        f"/api/v1/jobs/{created.json()['id']}",
        json={"status": "forwarded", "page_count": 3, "color_mode": "color"},
        headers=backend_headers,
    )
    assert updated.json()["color_mode"] == "color"
