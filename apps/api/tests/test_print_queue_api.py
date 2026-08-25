"""The queue page shows a person their place in line and nothing else about
anyone else.

Two things are load-bearing here and both are server-side, because a browser
is not where privacy or authorisation get decided:

- another person's job comes back with `document_name: null`, never with the
  title redacted in the UI;
- yield/restore act only on a job whose CUPS owner matches the caller, and
  only while it is still waiting.

The queue itself is read from cupsd (app/printers/print_queue.py) — a waiting
job has no database row at all — so these tests stand in for cupsd rather than
seeding the `jobs` table.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer
from app.printers.print_queue import (
    NORMAL_PRIORITY,
    YIELDED_PRIORITY,
    QueuedJob,
    remember_priority_before_yield,
)


async def _resolved(email: str):
    return (email, "alias", None)


PRINTER_ID = uuid.UUID("8142ccdb-195b-4acf-acfd-56bc52162b72")
OTHER_PRINTER_ID = uuid.UUID("629d2c72-31eb-426d-8d91-1ab629a84ff7")

# The dev break-glass account the other API tests log in with.
ME = "admin"
SOMEONE_ELSE = "agerukink@brookfieldr3.org"


def job(
    cups_job_id: int,
    owner: str,
    *,
    printer_id: uuid.UUID = PRINTER_ID,
    state: int = 3,
    priority: int = NORMAL_PRIORITY,
    name: str = "Choir Concert Programme.pdf",
) -> QueuedJob:
    return QueuedJob(
        cups_job_id=cups_job_id,
        printer_id=str(printer_id),
        owner=owner,
        source_host=None,
        document_name=name,
        size_bytes=1024,
        priority=priority,
        state=state,
        created_at=None,
    )


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
    async with session_factory() as session:
        session.add(Printer(id=PRINTER_ID, name="MS Choir Copier", ip_address="10.20.1.7"))
        session.add(Printer(id=OTHER_PRINTER_ID, name="HS Office Printer", ip_address="10.30.1.8"))
        await session.commit()
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
    response = client.post("/auth/login", json={"username": ME, "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def queue(monkeypatch):
    """Stands in for cupsd. Returns a setter so each test states the line it
    is describing."""
    jobs: list[QueuedJob] = []
    monkeypatch.setattr("app.routers.print_queue.queued_jobs", lambda: list(jobs))

    def set_jobs(*new: QueuedJob):
        jobs.clear()
        jobs.extend(new)

    return set_jobs


@pytest.fixture
def priority_calls(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "app.routers.print_queue.set_cups_job_priority",
        lambda cups_job_id, priority: calls.append((cups_job_id, priority)),
    )
    return calls


async def test_shows_the_whole_line_but_only_my_document_names(client, auth_headers, queue):
    queue(
        job(1, SOMEONE_ELSE, name="Discipline Letter - Redacted.pdf"),
        job(2, ME, name="Choir Concert Programme.pdf"),
    )
    response = client.get("/api/v1/print-queue", headers=auth_headers)
    assert response.status_code == 200, response.text
    [printer] = response.json()["queues"]
    assert printer["printer_name"] == "MS Choir Copier"
    assert printer["total_job_count"] == 2
    assert printer["my_job_count"] == 1

    theirs, mine = printer["jobs"]
    # They can see someone is ahead of them, and how big it is — that is what
    # the decision to yield rests on — but not what it is.
    assert theirs["mine"] is False
    assert theirs["document_name"] is None
    assert theirs["size_bytes"] == 1024
    assert theirs["position"] == 1
    assert mine["mine"] is True
    assert mine["document_name"] == "Choir Concert Programme.pdf"
    assert mine["can_yield"] is True


async def test_a_printer_i_have_nothing_on_is_not_listed(client, auth_headers, queue):
    queue(job(1, SOMEONE_ELSE, printer_id=OTHER_PRINTER_ID), job(2, ME))
    response = client.get("/api/v1/print-queue", headers=auth_headers)
    names = [printer["printer_name"] for printer in response.json()["queues"]]
    assert names == ["MS Choir Copier"]


async def test_empty_when_nothing_of_mine_is_waiting(client, auth_headers, queue):
    queue(job(1, SOMEONE_ELSE))
    response = client.get("/api/v1/print-queue", headers=auth_headers)
    assert response.json() == {"queues": [], "held": []}


async def test_a_held_job_sorts_behind_jobs_that_can_actually_print(client, auth_headers, queue):
    # The held job is the oldest, but cupsd will print the pending one first —
    # a held job waits on a person, not on the queue. Ordering by age here
    # would tell this person they are 2nd when they are next.
    queue(job(1, SOMEONE_ELSE, state=4), job(8, ME))
    [printer] = client.get("/api/v1/print-queue", headers=auth_headers).json()["queues"]
    assert [(row["cups_job_id"], row["position"]) for row in printer["jobs"]] == [(8, 1), (1, 2)]


async def test_the_job_being_printed_is_first_and_cannot_be_moved(client, auth_headers, queue):
    queue(job(9, ME, state=3), job(4, ME, state=5))
    [printer] = client.get("/api/v1/print-queue", headers=auth_headers).json()["queues"]
    printing, waiting = printer["jobs"]
    assert printing["cups_job_id"] == 4
    assert printing["state"] == "printing"
    assert printing["can_yield"] is False
    assert waiting["state"] == "waiting"
    assert waiting["can_yield"] is True


async def test_yield_drops_my_job_to_the_bottom(client, auth_headers, queue, priority_calls):
    queue(job(2, ME))
    response = client.post("/api/v1/print-queue/jobs/2/yield", headers=auth_headers)
    assert response.status_code == 204
    assert priority_calls == [(2, YIELDED_PRIORITY)]


async def test_a_job_cups_recorded_under_a_bare_username_is_still_mine(
    client, auth_headers, queue, priority_calls, monkeypatch
):
    """A Mac sends the local account name, not the address the person signs in
    with. Attribution already resolves that everywhere else; if the queue
    doesn't, its owner sees an empty page and a 404 on a job sitting in front
    of them."""
    monkeypatch.setattr(
        "app.routers.print_queue.resolve_user",
        lambda db, cups_user, source_host: _resolved(ME),
    )
    queue(job(5, "matt"))
    [printer] = client.get("/api/v1/print-queue", headers=auth_headers).json()["queues"]
    [row] = printer["jobs"]
    assert row["mine"] is True
    assert row["document_name"] == "Choir Concert Programme.pdf"
    assert client.post("/api/v1/print-queue/jobs/5/yield", headers=auth_headers).status_code == 204
    assert priority_calls == [(5, YIELDED_PRIORITY)]


async def test_a_bare_username_that_resolves_to_someone_else_is_not_mine(
    client, auth_headers, queue, priority_calls, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.print_queue.resolve_user",
        lambda db, cups_user, source_host: _resolved(SOMEONE_ELSE),
    )
    queue(job(5, "anna"))
    assert client.get("/api/v1/print-queue", headers=auth_headers).json()["queues"] == []
    assert client.post("/api/v1/print-queue/jobs/5/yield", headers=auth_headers).status_code == 404
    assert priority_calls == []


async def test_restore_puts_it_back_where_it_was(client, auth_headers, queue, priority_calls):
    queue(job(2, ME, priority=YIELDED_PRIORITY))
    remember_priority_before_yield(str(PRINTER_ID), 2, NORMAL_PRIORITY)
    response = client.post("/api/v1/print-queue/jobs/2/restore", headers=auth_headers)
    assert response.status_code == 204
    assert priority_calls == [(2, NORMAL_PRIORITY)]


async def test_restore_returns_an_unusual_priority_to_what_it_was(
    client, auth_headers, queue, priority_calls
):
    # A job that arrived at 20 goes back to 20, not to the default — restoring
    # it to 50 would move it ahead of jobs it was legitimately queued behind.
    queue(job(2, ME, priority=YIELDED_PRIORITY))
    remember_priority_before_yield(str(PRINTER_ID), 2, 20)
    client.post("/api/v1/print-queue/jobs/2/restore", headers=auth_headers)
    assert priority_calls == [(2, 20)]


async def test_a_low_priority_job_printops_never_lowered_cannot_be_raised(
    client, auth_headers, queue, priority_calls
):
    # Submitted at priority 1 by the client itself. PrintOps will not promote
    # a job it did not demote — that is the no-queue-jumping guarantee holding
    # in the one corner where it could quietly fail.
    queue(job(2, ME, priority=YIELDED_PRIORITY))
    response = client.post("/api/v1/print-queue/jobs/2/restore", headers=auth_headers)
    assert response.status_code == 400
    assert priority_calls == []


async def test_a_yielded_job_offers_restore_not_yield(client, auth_headers, queue):
    queue(job(2, ME, priority=YIELDED_PRIORITY))
    remember_priority_before_yield(str(PRINTER_ID), 2, NORMAL_PRIORITY)
    [printer] = client.get("/api/v1/print-queue", headers=auth_headers).json()["queues"]
    [mine] = printer["jobs"]
    assert mine["yielded"] is True
    assert mine["can_yield"] is False
    assert mine["can_restore"] is True


async def test_cannot_touch_someone_elses_job(client, auth_headers, queue, priority_calls):
    queue(job(7, SOMEONE_ELSE))
    response = client.post("/api/v1/print-queue/jobs/7/yield", headers=auth_headers)
    # 404, not 403: a different answer here would confirm to anyone guessing
    # job ids that a job exists and whose it is.
    assert response.status_code == 404
    assert priority_calls == []


async def test_cannot_move_a_job_that_is_already_printing(
    client, auth_headers, queue, priority_calls
):
    queue(job(3, ME, state=5))
    response = client.post("/api/v1/print-queue/jobs/3/yield", headers=auth_headers)
    assert response.status_code == 400
    assert "already started printing" in response.json()["detail"]
    assert priority_calls == []


async def test_cannot_move_a_held_job(client, auth_headers, queue, priority_calls):
    queue(job(3, ME, state=4))
    response = client.post("/api/v1/print-queue/jobs/3/yield", headers=auth_headers)
    assert response.status_code == 400
    assert priority_calls == []


async def test_cupsd_not_answering_is_not_an_empty_queue(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.routers.print_queue.queued_jobs", lambda: None)
    response = client.get("/api/v1/print-queue", headers=auth_headers)
    assert response.status_code == 503


async def test_the_queue_needs_a_login(client, queue):
    queue(job(1, ME))
    assert client.get("/api/v1/print-queue").status_code == 401
