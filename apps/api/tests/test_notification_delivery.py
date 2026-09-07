"""Getting a raised notification out, and coping when that fails.

The failure modes here are not "a message is late". They are: a dead channel
turning into a permanently growing queue and a permanently failing loop; a
restart resetting every backoff so a struggling endpoint gets hammered; and
duplicates, which are the thing people actually notice and complain about.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.notification import (
    MAX_DELIVERY_ATTEMPTS,
    NotificationChannel,
    NotificationEvent,
    NotificationSettings,
)
from app.notifications import delivery
from app.notifications.delivery import (
    DeliveryError,
    backoff_delay,
    deliver_pending,
    render_text,
    webhook_payload,
)

NOW = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


def _aware(value: datetime) -> datetime:
    """SQLite has no timezone type, so a value read back from it comes out
    naive while the one still in memory is aware — and comparing the two
    raises rather than returning False. Postgres, which is what production
    runs, does not have this problem; app/notifications/conditions.py
    normalises for the same reason."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _setup(db, *, enabled=True, notifications_on=True):
    db.add(NotificationSettings(enabled=notifications_on))
    db.add(
        NotificationChannel(
            kind="webhook", name="Helpdesk", target="https://chat.example.org/hook", enabled=enabled
        )
    )
    db.add(
        NotificationEvent(
            kind="printer.error",
            dedupe_key="printer.error:p1:media-jam",
            title="ES Library: media-jam",
            body="ES / Library\nPaper jam",
            created_at=NOW,
            next_attempt_at=NOW,
        )
    )
    await db.commit()


def _sender(monkeypatch, behaviour):
    calls: list = []

    async def fake(channel, event):
        calls.append((channel.name, event.title))
        result = behaviour(len(calls))
        if isinstance(result, Exception):
            raise result

    monkeypatch.setattr(delivery, "send_to_channel", fake)
    return calls


@pytest.mark.asyncio
async def test_a_successful_send_marks_the_event_delivered(db, monkeypatch):
    await _setup(db)
    calls = _sender(monkeypatch, lambda n: None)

    assert await deliver_pending(db, now=NOW) == 1
    assert len(calls) == 1

    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.delivered_at is not None
    assert event.last_error is None

    channel = (await db.execute(select(NotificationChannel))).scalar_one()
    assert channel.last_success_at is not None
    assert channel.last_error is None


@pytest.mark.asyncio
async def test_a_delivered_event_is_never_sent_twice(db, monkeypatch):
    """Duplicates are the failure people notice. Two passes, one message."""
    await _setup(db)
    calls = _sender(monkeypatch, lambda n: None)

    await deliver_pending(db, now=NOW)
    await deliver_pending(db, now=NOW + timedelta(hours=1))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_failure_is_recorded_and_retried_later_not_immediately(db, monkeypatch):
    await _setup(db)
    _sender(monkeypatch, lambda n: DeliveryError("HTTP 503: upstream down"))

    assert await deliver_pending(db, now=NOW) == 0

    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.delivered_at is None
    assert event.attempts == 1
    assert "503" in event.last_error
    # The next attempt is in the future, so a loop running every minute does
    # not turn one failing channel into a tight retry loop against it.
    assert _aware(event.next_attempt_at) > NOW

    channel = (await db.execute(select(NotificationChannel))).scalar_one()
    assert "503" in channel.last_error


@pytest.mark.asyncio
async def test_an_event_in_backoff_is_left_alone(db, monkeypatch):
    await _setup(db)
    _sender(monkeypatch, lambda n: DeliveryError("HTTP 503"))
    await deliver_pending(db, now=NOW)

    calls = _sender(monkeypatch, lambda n: None)
    await deliver_pending(db, now=NOW + timedelta(seconds=10))
    assert calls == []

    await deliver_pending(db, now=NOW + timedelta(hours=2))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_transient_failure_is_eventually_given_up_on(db, monkeypatch):
    """Even something that might come back does not get retried forever, or one
    dead channel becomes a permanently growing queue."""
    await _setup(db)
    _sender(monkeypatch, lambda n: DeliveryError("HTTP 503: still down"))

    at = NOW
    for _ in range(MAX_DELIVERY_ATTEMPTS + 3):
        await deliver_pending(db, now=at)
        at += timedelta(hours=2)

    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.attempts == MAX_DELIVERY_ATTEMPTS
    assert event.delivered_at is None


@pytest.mark.asyncio
async def test_a_permanent_rejection_is_not_retried_at_all(db, monkeypatch):
    """A revoked URL or a deleted space answers the same way every time.
    Sending the identical request seven more times is noise against somebody
    else's server and delays nothing but the giving up."""
    await _setup(db)
    calls = _sender(monkeypatch, lambda n: DeliveryError("HTTP 404: no such space", permanent=True))

    await deliver_pending(db, now=NOW)
    await deliver_pending(db, now=NOW + timedelta(days=1))

    assert len(calls) == 1
    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.attempts == MAX_DELIVERY_ATTEMPTS
    assert event.next_attempt_at is None


@pytest.mark.asyncio
async def test_turning_notifications_off_stops_what_is_already_queued(db, monkeypatch):
    """The switch governs sending, not just raising. Otherwise unchecking it
    produces one last burst — the opposite of what the admin asked for."""
    await _setup(db, notifications_on=False)
    calls = _sender(monkeypatch, lambda n: None)

    assert await deliver_pending(db, now=NOW) == 0
    assert calls == []
    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.attempts == 0


@pytest.mark.asyncio
async def test_nothing_is_sent_when_no_channel_is_configured(db, monkeypatch):
    db.add(
        NotificationEvent(
            kind="printer.error",
            dedupe_key="k",
            title="t",
            body="b",
            created_at=NOW,
            next_attempt_at=NOW,
        )
    )
    await db.commit()
    calls = _sender(monkeypatch, lambda n: None)

    assert await deliver_pending(db, now=NOW) == 0
    assert calls == []
    # And crucially the event is untouched, not consumed — configuring a
    # channel later should deliver what has been waiting.
    event = (await db.execute(select(NotificationEvent))).scalar_one()
    assert event.attempts == 0


@pytest.mark.asyncio
async def test_a_disabled_channel_receives_nothing(db, monkeypatch):
    await _setup(db, enabled=False)
    calls = _sender(monkeypatch, lambda n: None)

    assert await deliver_pending(db, now=NOW) == 0
    assert calls == []


# --- classification and shape ----------------------------------------------


def test_backoff_grows_and_is_capped():
    """Growing matters; the cap matters more. An unbounded backoff on a channel
    that comes back after a day leaves the queue silent for a day after it
    did."""
    assert backoff_delay(1) < backoff_delay(3) < backoff_delay(5)
    assert backoff_delay(99) == timedelta(seconds=delivery.BACKOFF_CAP_SECONDS)


@pytest.mark.parametrize(
    ("status_code", "permanent"),
    [
        (400, True),
        (403, True),
        (404, True),
        # A 4xx that explicitly means "later" — the one exception.
        (429, False),
        (500, False),
        (503, False),
    ],
)
@pytest.mark.asyncio
async def test_client_errors_are_permanent_except_rate_limiting(
    status_code, permanent, monkeypatch
):
    channel = NotificationChannel(kind="webhook", name="c", target="https://example.org/hook")
    event = NotificationEvent(kind="k", dedupe_key="d", title="t", body="b")

    class _Response:
        def __init__(self):
            self.status_code = status_code
            self.text = "nope"

        @property
        def is_success(self):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Response()

    monkeypatch.setattr(delivery.httpx, "AsyncClient", lambda **k: _Client())

    with pytest.raises(DeliveryError) as caught:
        await delivery.send_to_channel(channel, event)
    assert caught.value.permanent is permanent


@pytest.mark.asyncio
async def test_a_network_error_is_never_permanent(monkeypatch):
    """The far end may well be back in a minute; a DNS blip must not retire the
    event."""
    channel = NotificationChannel(kind="webhook", name="c", target="https://example.org/hook")
    event = NotificationEvent(kind="k", dedupe_key="d", title="t", body="b")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(delivery.httpx, "AsyncClient", lambda **k: _Client())

    with pytest.raises(DeliveryError) as caught:
        await delivery.send_to_channel(channel, event)
    assert caught.value.permanent is False


def test_discord_gets_content_and_everyone_else_gets_text():
    """Google Chat, Slack and Teams render `text`. Discord ignores it, treats
    the message as empty and answers 400 — and the settings page advertises
    Discord, so every real Discord notification would have failed."""
    event = NotificationEvent(kind="k", dedupe_key="d", title="t", body="b")

    assert "text" in webhook_payload(event, "https://chat.googleapis.com/v1/spaces/x")
    assert "text" in webhook_payload(event, "https://hooks.slack.com/services/x")
    assert "text" in webhook_payload(event, "https://example.webhook.office.com/x")
    assert "content" in webhook_payload(event, "https://discord.com/api/webhooks/1/x")
    assert "content" in webhook_payload(event, "https://ptb.discord.com/api/webhooks/1/x")
    # No URL at all must not silently become the Discord shape.
    assert "text" in webhook_payload(event)


def test_the_message_says_what_and_where():
    """A message that does not say which printer, or which room, costs somebody
    a lookup before they can act on it."""
    event = NotificationEvent(
        kind="printer.error",
        dedupe_key="d",
        title="ES Library Printer: media-jam",
        body="ES / Library\nPaper jam in the output tray",
        severity="warning",
    )
    text = render_text(event)

    assert "ES Library Printer" in text
    assert "ES / Library" in text
    assert webhook_payload(event, "https://chat.googleapis.com/x") == {"text": text}


@pytest.mark.asyncio
async def test_an_unsupported_channel_kind_is_permanent(monkeypatch):
    """Email arrives as a second kind. Until it does, a row claiming to be one
    must not be retried eight times against code that cannot send it."""
    channel = NotificationChannel(kind="email", name="c", target="admin@example.org")
    event = NotificationEvent(kind="k", dedupe_key="d", title="t", body="b")

    with pytest.raises(DeliveryError) as caught:
        await delivery.send_to_channel(channel, event)
    assert caught.value.permanent is True
