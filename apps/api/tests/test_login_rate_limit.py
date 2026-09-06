"""The break-glass admin password is one short secret on a host that
answers from the public internet. Until 0.72.0 it could be guessed as
fast as the server would run bcrypt."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.routers.auth import login_limiter


@pytest.fixture(autouse=True)
def clean_limiter():
    login_limiter.reset()
    yield
    login_limiter.reset()


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
    """A successful sign-in reads the session-timeout settings, so this
    needs a real database even though the password check does not."""

    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _attempt(client, password="wrong", forwarded=None):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return client.post(
        "/auth/login",
        json={"username": "admin", "password": password},
        headers=headers,
    )


def test_guessing_is_cut_off(client):
    for _ in range(8):
        assert _attempt(client).status_code == 401
    # The ninth is refused without the password being checked at all.
    assert _attempt(client).status_code == 429


def test_the_refusal_says_to_wait(client):
    for _ in range(8):
        _attempt(client)
    assert "try again" in _attempt(client).json()["detail"].lower()


def test_a_correct_password_clears_the_count(client):
    """Somebody who mistypes twice and then gets it right is not two
    attempts from being locked out for five minutes."""
    for _ in range(7):
        assert _attempt(client).status_code == 401

    assert _attempt(client, password="changeme").status_code == 200

    for _ in range(8):
        assert _attempt(client).status_code == 401


def test_one_caller_cannot_lock_out_another(client):
    """The API listens on loopback only, so every request arrives from
    Caddy and the socket address is the same for everybody. Keying on
    that would let one wrong password shut the district out."""
    for _ in range(8):
        assert _attempt(client, forwarded="203.0.113.9").status_code == 401
    assert _attempt(client, forwarded="203.0.113.9").status_code == 429

    assert _attempt(client, forwarded="198.51.100.4").status_code == 401


def test_the_account_is_never_locked(client):
    """Locking the account would hand any passer-by the ability to shut
    the only local admin out of a print server."""
    for _ in range(8):
        _attempt(client, forwarded="203.0.113.9")
    assert _attempt(client, forwarded="203.0.113.9").status_code == 429

    # A different address, right password: still in.
    assert _attempt(client, password="changeme", forwarded="198.51.100.4").status_code == 200


def test_the_api_docs_are_not_served(client):
    """They describe every endpoint, schema and parameter to anyone who
    finds the hostname, and the reverse proxy forwards /docs from the
    public internet."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
