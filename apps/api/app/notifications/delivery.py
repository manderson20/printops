"""Getting a raised event to a channel, and coping when that fails.

The webhook body is deliberately the lowest common denominator: a JSON object
with a `text` field. Google Chat, Slack, Teams and Discord all accept an
incoming-webhook URL and all render that field, so one implementation covers
the four things a district is realistically using without four sets of
vendor-specific formatting to keep working.

Failure handling is the part worth reading. A webhook URL that has been revoked
will never succeed, so retrying forever turns one dead channel into a
permanently growing queue and a permanently failing loop. Attempts are capped,
backoff is stored on the row rather than held in the loop (a restart must not
reset every failing delivery to "try now" and hammer a channel that is already
down), and the error is recorded where an admin can see it instead of only in
the journal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    MAX_DELIVERY_ATTEMPTS,
    NotificationChannel,
    NotificationEvent,
)
from app.notifications.settings import get_or_create_notification_settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10.0

# Doubling from a minute, capped at an hour. The cap matters more than the
# curve: an unbounded backoff on a channel that comes back after a day would
# leave the queue silent for a day after it did.
BACKOFF_BASE_SECONDS = 60
BACKOFF_CAP_SECONDS = 60 * 60

# How many events one pass will send. A bound so that a channel restored after
# a long outage delivers its backlog over a few passes instead of in one burst
# that the receiving side rate-limits and rejects.
BATCH_LIMIT = 20


def backoff_delay(attempts: int) -> timedelta:
    seconds = min(BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)), BACKOFF_CAP_SECONDS)
    return timedelta(seconds=seconds)


def render_text(event: NotificationEvent) -> str:
    """What a person reads on their phone.

    Title first because most clients truncate, and the title is the part that
    decides whether somebody walks to the printer.
    """
    prefix = "⚠️" if event.severity == "warning" else "ℹ️"
    return f"{prefix} *{event.title}*\n{event.body}"


# Discord is the odd one out. Google Chat, Slack and Teams all render `text`;
# Discord ignores it and treats the message as empty, answering 400. Detected
# from the URL rather than asked for as a per-channel setting, because a person
# pasting a webhook URL should not also have to tell PrintOps which product
# they just pasted — and getting that dropdown wrong fails silently.
DISCORD_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")


def webhook_payload(event: NotificationEvent, target: str = "") -> dict[str, str]:
    host = urlparse(target).hostname or ""
    if host in DISCORD_HOSTS or host.endswith(".discord.com"):
        return {"content": render_text(event)}
    return {"text": render_text(event)}


class DeliveryError(Exception):
    """A send failed. `permanent` means retrying will not help."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


async def send_to_channel(channel: NotificationChannel, event: NotificationEvent) -> None:
    """POST one event to one channel. Raises DeliveryError on failure."""
    if channel.kind != "webhook":
        raise DeliveryError(f"Unsupported channel kind: {channel.kind!r}", permanent=True)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                channel.target, json=webhook_payload(event, channel.target)
            )
    except httpx.HTTPError as exc:
        # Network-level: the far end may well be back in a minute.
        raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc

    if response.is_success:
        return

    # 4xx means this request is wrong and will stay wrong — a revoked URL, a
    # deleted space, a malformed body. Retrying is just noise against somebody
    # else's server. 429 is the exception: it is a 4xx that explicitly means
    # "later".
    permanent = 400 <= response.status_code < 500 and response.status_code != 429
    raise DeliveryError(f"HTTP {response.status_code}: {response.text[:200]}", permanent=permanent)


async def deliver_pending(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Send what is due. Returns how many events were delivered.

    Commits per event rather than per batch: a crash halfway through a backlog
    should not re-send everything that already arrived. Duplicate messages are
    the failure mode people notice.
    """
    now = now or datetime.now(UTC)

    # The global switch governs sending, not just raising. An admin who
    # unchecks "Send notifications" means stop, including for whatever is
    # already queued or backing off — otherwise turning it off produces a final
    # burst, which is the opposite of what they asked for.
    settings = await get_or_create_notification_settings(db)
    if not settings.enabled:
        return 0

    channels = (
        (await db.execute(select(NotificationChannel).where(NotificationChannel.enabled.is_(True))))
        .scalars()
        .all()
    )
    if not channels:
        return 0

    due = (
        (
            await db.execute(
                select(NotificationEvent)
                .where(
                    NotificationEvent.delivered_at.is_(None),
                    NotificationEvent.attempts < MAX_DELIVERY_ATTEMPTS,
                    NotificationEvent.next_attempt_at <= now,
                )
                .order_by(NotificationEvent.created_at)
                .limit(BATCH_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    delivered = 0
    for event in due:
        event.attempts += 1
        errors: list[str] = []
        retryable = False

        for channel in channels:
            try:
                await send_to_channel(channel, event)
            except DeliveryError as exc:
                errors.append(f"{channel.name}: {exc}")
                channel.last_error = str(exc)[:500]
                if exc.permanent:
                    logger.warning(
                        "Notification channel %s rejected an event permanently: %s",
                        channel.name,
                        exc,
                    )
                else:
                    retryable = True
            else:
                channel.last_error = None
                channel.last_success_at = now

        if errors:
            event.last_error = "; ".join(errors)[:1000]
            if not retryable:
                # Every destination rejected it in a way that will not change:
                # a revoked URL, a deleted space, a malformed request. Trying
                # the identical request seven more times is noise against
                # somebody else's server and delays nothing but the giving up.
                event.attempts = MAX_DELIVERY_ATTEMPTS
                event.next_attempt_at = None
                logger.error(
                    "Notification %s was permanently rejected by every channel: %s",
                    event.id,
                    event.last_error,
                )
            else:
                event.next_attempt_at = now + backoff_delay(event.attempts)
                if event.attempts >= MAX_DELIVERY_ATTEMPTS:
                    logger.error(
                        "Giving up on notification %s after %s attempts: %s",
                        event.id,
                        event.attempts,
                        event.last_error,
                    )
        else:
            # Delivered when it reached every enabled channel. Partial success
            # is treated as failure on purpose: retrying costs a duplicate on
            # the channels that worked, and a missed alert costs the thing the
            # feature exists to prevent.
            event.delivered_at = now
            event.last_error = None
            delivered += 1

        await db.commit()

    return delivered
