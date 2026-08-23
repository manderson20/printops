"""app/routers/jobs.py — one CUPS job, one job in the reports.

cupsd re-runs the backend for every retry of a job, and the backend registers a
new row each time. Job 4584 on the ES Veronica Copier left 51 rows in August,
every one of them recorded as a failure, so the reports showed 51 failed jobs
where a person had sent one. The failure count is the number an admin acts on.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.routers.jobs import SUPERSEDES_WITHIN


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


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(name="ES Veronica Copier", ip_address="10.10.3.36")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return printer.id


@pytest.fixture
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


def _register(client, headers, printer_id, cups_job_id=4584):
    response = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "printer_id": str(printer_id),
            "cups_job_id": cups_job_id,
            "submitted_by": "ateacher",
            "document_name": "Cupcake toppers - Google Docs",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _rows(db_session_factory, printer_id):
    async with db_session_factory() as session:
        result = await session.execute(
            select(Job).where(Job.printer_id == printer_id).order_by(Job.created_at)
        )
        return list(result.scalars().all())


async def test_a_retry_supersedes_the_attempt_before_it(
    client, backend_headers, printer_id, db_session_factory
):
    first = _register(client, backend_headers, printer_id)
    second = _register(client, backend_headers, printer_id)

    by_id = {str(row.id): row for row in await _rows(db_session_factory, printer_id)}
    assert by_id[first["id"]].status == "cancelled"
    assert "Superseded" in by_id[first["id"]].error_message
    assert by_id[first["id"]].completed_at is not None
    # The live one is untouched — it is the row that will say what happened.
    assert by_id[second["id"]].status == "forwarding"


async def test_fifty_one_retries_leave_one_open_row(
    client, backend_headers, printer_id, db_session_factory
):
    """The shape of the real incident."""
    for _ in range(51):
        _register(client, backend_headers, printer_id)

    rows = await _rows(db_session_factory, printer_id)
    assert len(rows) == 51, "every attempt is still on the record"
    assert len([row for row in rows if row.status == "forwarding"]) == 1
    assert all("Superseded" in row.error_message for row in rows if row.status == "cancelled")


async def test_a_different_cups_job_is_not_touched(
    client, backend_headers, printer_id, db_session_factory
):
    other = _register(client, backend_headers, printer_id, cups_job_id=4585)
    _register(client, backend_headers, printer_id, cups_job_id=4584)
    _register(client, backend_headers, printer_id, cups_job_id=4584)

    rows = {str(row.id): row for row in await _rows(db_session_factory, printer_id)}
    assert rows[other["id"]].status == "forwarding"


async def test_a_delivered_job_is_never_rewritten(
    client, backend_headers, printer_id, db_session_factory
):
    """CUPS job numbers are reused once the spool is cleared. A delivery that
    happened stays on the record whatever number turns up later."""
    delivered = _register(client, backend_headers, printer_id)
    client.patch(
        f"/api/v1/jobs/{delivered['id']}",
        headers=backend_headers,
        json={"status": "forwarded", "page_count": 12},
    )

    _register(client, backend_headers, printer_id)

    rows = {str(row.id): row for row in await _rows(db_session_factory, printer_id)}
    assert rows[delivered["id"]].status == "forwarded"
    assert rows[delivered["id"]].page_count == 12


async def test_an_old_row_with_the_same_number_is_left_alone(
    client, backend_headers, printer_id, db_session_factory
):
    """Job numbers reset when the spool is cleared, so a row from last week
    that shares a number is a different job, not an earlier attempt."""
    async with db_session_factory() as session:
        stale = Job(
            id=uuid.uuid4(),
            printer_id=printer_id,
            cups_job_id=4584,
            status="failed",
            submitted_by="someone.else",
        )
        session.add(stale)
        await session.commit()
        stale.created_at = datetime.now(UTC) - SUPERSEDES_WITHIN - timedelta(hours=1)
        await session.commit()
        stale_id = str(stale.id)

    _register(client, backend_headers, printer_id)

    rows = {str(row.id): row for row in await _rows(db_session_factory, printer_id)}
    assert rows[stale_id].status == "failed"
    assert rows[stale_id].error_message is None
