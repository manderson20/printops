"""Confirming a printer's move to a different host.

A device switched to TLS-only IPP redirects its old address to its new one,
and PrintOps adopts the port, scheme and path automatically — that is the same
machine, reached differently, and CUPS cannot follow a redirect so somebody has
to. A redirect naming a different *host* is a different claim: it says this is
now a different machine. Taking a device's word for that would let a
misconfigured unit, or a recycled DHCP address, quietly redirect other people's
documents — including held ones waiting at a release printer — to a box nobody
chose, with every dashboard still green.

So it waits for a person. These cover what happens when that person says yes,
says no, or says yes to something that turns out not to be there.
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer
from app.printers.ipp_client import PrinterProbeError, ProbeResult

PENDING = {
    "host": "10.50.1.99",
    "port": 443,
    "tls": True,
    "path": "/ipp/print",
    "seen_at": "2026-08-23T22:00:00+00:00",
}


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
            name="LCACTC - GA Kyocera",
            ip_address="10.50.1.37",
            port=631,
            use_tls=False,
            pending_redirect=PENDING,
        )
        session.add(printer)
        await session.commit()
        return str(printer.id)


@pytest.fixture(autouse=True)
def no_queue_sync(monkeypatch):
    from app.routers import printers as printers_router

    monkeypatch.setattr(printers_router, "sync_queue", lambda pid, is_virtual=False: None)


def _answers(path="/ipp/print"):
    return ProbeResult(
        raw_attributes={"printer-make-and-model": "ECOSYS P8060cdn"}, resolved_path=path
    )


async def test_confirming_moves_the_printer_to_the_new_address(client, auth_headers, printer_id):
    with patch("app.routers.printers.probe_printer", return_value=_answers()):
        response = client.post(
            f"/api/v1/printers/{printer_id}/redirect/confirm", headers=auth_headers
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ip_address"] == "10.50.1.99"
    assert body["port"] == 443
    assert body["use_tls"] is True
    assert body["pending_redirect"] is None


async def test_a_move_is_verified_before_it_is_made(client, auth_headers, printer_id):
    """The device's claim is evidence, not proof. Moving a printer to an
    address that does not answer takes one that is merely unreachable and
    points it somewhere it cannot print from either."""
    with patch(
        "app.routers.printers.probe_printer",
        side_effect=PrinterProbeError("Could not reach an IPP printer at 10.50.1.99:443"),
    ):
        response = client.post(
            f"/api/v1/printers/{printer_id}/redirect/confirm", headers=auth_headers
        )

    assert response.status_code == 502
    assert "left where it is" in response.json()["detail"]

    printer = client.get(f"/api/v1/printers/{printer_id}", headers=auth_headers).json()
    assert printer["ip_address"] == "10.50.1.37", "unchanged"
    assert printer["pending_redirect"] is not None, "still waiting, not silently dropped"


async def test_dismissing_forgets_the_suggestion(client, auth_headers, printer_id):
    response = client.post(f"/api/v1/printers/{printer_id}/redirect/dismiss", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["pending_redirect"] is None
    assert response.json()["ip_address"] == "10.50.1.37"


async def test_confirming_nothing_is_not_a_silent_success(client, auth_headers, printer_id):
    client.post(f"/api/v1/printers/{printer_id}/redirect/dismiss", headers=auth_headers)

    response = client.post(f"/api/v1/printers/{printer_id}/redirect/confirm", headers=auth_headers)

    assert response.status_code == 404


async def test_only_an_admin_can_move_a_printer(client, printer_id):
    response = client.post(f"/api/v1/printers/{printer_id}/redirect/confirm")
    assert response.status_code in (401, 403)


async def test_a_stale_path_override_is_dropped_when_the_host_changes(
    client, auth_headers, db_session_factory
):
    """effective_ipp_path prefers an explicit override over anything
    discovered. Left standing across a host move, it would beat the path just
    verified on the new machine, and the queue would be rebuilt against a path
    nobody has confirmed answers there — a printer that reads as moved and
    cannot print."""
    async with db_session_factory() as session:
        printer = Printer(
            id=uuid.uuid4(),
            name="Overridden Printer",
            ip_address="10.50.1.37",
            port=631,
            use_tls=False,
            ipp_path="/printers/old-choice",
            pending_redirect=PENDING,
        )
        session.add(printer)
        await session.commit()
        pid = str(printer.id)

    with patch("app.routers.printers.probe_printer", return_value=_answers("/ipp/print")):
        response = client.post(f"/api/v1/printers/{pid}/redirect/confirm", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ipp_path"] is None, "the override was chosen for the old machine"
    assert body["ipp_path_detected"] == "/ipp/print"


async def test_an_override_that_already_matches_is_left_alone(
    client, auth_headers, db_session_factory
):
    """Nothing to correct: the admin's choice and the verified path agree."""
    async with db_session_factory() as session:
        printer = Printer(
            id=uuid.uuid4(),
            name="Agreeing Printer",
            ip_address="10.50.1.37",
            port=631,
            use_tls=False,
            ipp_path="/ipp/print",
            pending_redirect=PENDING,
        )
        session.add(printer)
        await session.commit()
        pid = str(printer.id)

    with patch("app.routers.printers.probe_printer", return_value=_answers("/ipp/print")):
        response = client.post(f"/api/v1/printers/{pid}/redirect/confirm", headers=auth_headers)

    assert response.json()["ipp_path"] == "/ipp/print"
