"""Deciding whether something is worth telling somebody.

Delivery is a POST. What decides whether this feature is kept switched on is
everything in this file: a notifier that cries wolf gets muted, and once it is
muted the message that mattered is muted with it.

Two mechanisms, and they do different jobs.

**Conditions, not events.** A printer offline for three days is one condition
observed by 4,320 poll cycles. Callers report what they *see* on every pass —
`observe()` is called unconditionally — and only a transition into a condition
can raise anything. The dedupe is a uniquely-keyed row, so a second observation
cannot create a second one even if two loops race.

**A settle delay.** A condition must still be there `settle_minutes` after it
was first seen. This is what stops a jam somebody clears in two minutes from
becoming a message, and it costs nothing but a timestamp: the state row is
created on first sight, and the raise happens on a later pass that finds the
condition still present and the window elapsed. No scheduler, no timers, no
work queued for something that may never need doing.

Events are written into the caller's session, never committed here, so the
alert and the state change that caused it land together or not at all — the
same property app/audit/record.py rests on. An alert about a status that rolled
back would be a lie, and a missing alert for one that committed would be worse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    NotificationEvent,
    NotificationSettings,
    NotificationState,
)

# Kinds. Dotted like audit actions, and for the same reason: the prefix is the
# grouping, so nothing needs a separate category column.
PRINTER_ATTENTION = "printer.attention"
PRINTER_ERROR = "printer.error"
TONER_LOW = "toner.low"
QUOTA_EXCEEDED = "quota.exceeded"

# Which settings flag governs which kind, so an admin who finds one trigger
# noisy can silence that one rather than the whole feature. Somebody turning
# off one trigger is a much better outcome than somebody turning off
# notifications.
ENABLED_FLAG: dict[str, str] = {
    PRINTER_ATTENTION: "notify_printer_attention",
    PRINTER_ERROR: "notify_printer_error",
    TONER_LOW: "notify_toner_low",
    QUOTA_EXCEEDED: "notify_quota_exceeded",
}


def kind_enabled(settings: NotificationSettings | None, kind: str) -> bool:
    if settings is None or not settings.enabled:
        return False
    return bool(getattr(settings, ENABLED_FLAG[kind], False))


async def observe(
    db: AsyncSession,
    settings: NotificationSettings | None,
    *,
    kind: str,
    dedupe_key: str,
    title: str,
    body: str,
    subject_type: str | None = None,
    subject_id: Any = None,
    severity: str = "warning",
    now: datetime | None = None,
) -> NotificationEvent | None:
    """Report that a condition is currently true. Call on every pass.

    Returns the event if this observation raised one, which happens at most
    once per condition — on the first pass that finds it still present after the
    settle window. Returns None every other time, including while notifications
    are switched off, so callers do not need to check first.

    Does not commit. The caller's transaction carries it.
    """
    now = now or datetime.now(UTC)

    # The state row is kept even when the kind is switched off, deliberately.
    # Turning a trigger back on should not immediately fire for conditions that
    # have been quietly true for a week — those are not news, and a burst of
    # them on the first poll after re-enabling is exactly how somebody decides
    # the feature is broken.
    state = (
        await db.execute(
            select(NotificationState).where(NotificationState.dedupe_key == dedupe_key)
        )
    ).scalar_one_or_none()

    if state is None:
        db.add(
            NotificationState(
                dedupe_key=dedupe_key,
                kind=kind,
                subject_type=subject_type,
                subject_id=str(subject_id) if subject_id is not None else None,
                first_seen_at=now,
                last_seen_at=now,
                # Backdated so a condition first seen while the kind was off is
                # treated as already-known rather than new.
                notified_at=None if kind_enabled(settings, kind) else now,
            )
        )
        return None

    state.last_seen_at = now
    if state.notified_at is not None:
        return None
    if not kind_enabled(settings, kind):
        return None

    settle = timedelta(minutes=max(0, settings.settle_minutes if settings else 0))
    first_seen = state.first_seen_at
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=UTC)
    if now - first_seen < settle:
        return None

    state.notified_at = now
    event = NotificationEvent(
        kind=kind,
        dedupe_key=dedupe_key,
        subject_type=subject_type,
        subject_id=str(subject_id) if subject_id is not None else None,
        title=title,
        body=body,
        severity=severity,
        created_at=now,
        next_attempt_at=now,
    )
    db.add(event)
    return event


async def clear(db: AsyncSession, dedupe_key: str) -> None:
    """The condition is no longer true.

    Deletes the state row, so the next occurrence is a fresh transition and does
    get reported. No "resolved" message is sent: that doubles the traffic, and
    the first version needs to earn its place in people's attention before it
    starts spending more of it.
    """
    await db.execute(delete(NotificationState).where(NotificationState.dedupe_key == dedupe_key))


async def clear_missing(db: AsyncSession, *, prefix: str, still_true: set[str]) -> None:
    """Clear every active condition under `prefix` that was not observed.

    The counterpart to calling observe() only for what is currently wrong: a
    caller that stops reporting a condition has to say so somehow, and asking
    each one to remember what it reported last time is how conditions get stuck
    active forever — after which the real recurrence is silently deduped away
    against a stale row.
    """
    rows = (
        (
            await db.execute(
                select(NotificationState).where(NotificationState.dedupe_key.like(f"{prefix}%"))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.dedupe_key not in still_true:
            await db.delete(row)
