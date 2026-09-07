import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# How long a condition must persist before anybody is told about it.
#
# The single most important number here. A printer jam somebody clears in two
# minutes is not worth a message, and a feature that sends one is a feature
# people mute — after which the message that mattered is muted too. Ten minutes
# is longer than a walk to the machine and shorter than an absence anybody
# would call a problem.
DEFAULT_SETTLE_MINUTES = 10

# A cartridge below this is worth knowing about before it runs out mid-job.
DEFAULT_TONER_LOW_PERCENT = 15

# Delivery gives up after this many attempts and leaves the failure on the row.
# Not infinite: a webhook URL that has been revoked will never succeed, and
# retrying it forever turns one dead channel into a permanently growing queue.
MAX_DELIVERY_ATTEMPTS = 8


class NotificationSettings(Base, TimestampMixin):
    """Singleton, same pattern as SyslogSettings/AuditSettings.

    `enabled` defaults false like every other settings model that talks to
    something outside this box — nothing is sent until an admin opts in and
    names somewhere to send it.
    """

    __tablename__ = "notification_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    settle_minutes: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_SETTLE_MINUTES, server_default=str(DEFAULT_SETTLE_MINUTES)
    )
    toner_low_percent: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_TONER_LOW_PERCENT, server_default=str(DEFAULT_TONER_LOW_PERCENT)
    )

    # Per-kind switches, so an admin who finds one trigger noisy can silence
    # that one instead of the whole feature. Turning everything off one trigger
    # at a time is a much better outcome than turning notifications off.
    notify_printer_attention: Mapped[bool] = mapped_column(default=True, server_default="true")
    notify_printer_error: Mapped[bool] = mapped_column(default=True, server_default="true")
    notify_toner_low: Mapped[bool] = mapped_column(default=True, server_default="true")
    notify_quota_exceeded: Mapped[bool] = mapped_column(default=False, server_default="false")


class NotificationChannel(Base, TimestampMixin):
    """Somewhere to send things. One row per destination.

    A table rather than a column on the settings singleton because a district
    plausibly wants two — the helpdesk space and the network admin's — and
    because email arrives next as a second `kind` rather than a schema change.
    """

    __tablename__ = "notification_channels"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # "webhook" today; "email" next.
    kind: Mapped[str] = mapped_column(String, default="webhook", server_default="webhook")
    name: Mapped[str] = mapped_column(String)
    # The webhook URL. Stored as given: an incoming-webhook URL is a bearer
    # credential in URL clothing, so it is never echoed back by the API — see
    # app/schemas/notification.py, which returns a redacted form.
    target: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")

    # Set when a delivery through this channel fails, cleared on the next
    # success, so a channel that has quietly stopped working is visible in
    # Settings rather than only to whoever reads the journal.
    last_error: Mapped[str | None] = mapped_column(String, default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class NotificationState(Base):
    """One row per *currently active* condition. This is the dedupe.

    The distinction that makes the feature usable: a condition is a state, not
    an event. A printer that has been offline for three days is one condition
    seen by 4,320 poll cycles, and must produce one message rather than 4,320.
    So the poll loop reports what it sees on every pass, and only a transition
    into a condition — a `dedupe_key` with no row yet — can raise anything.

    `first_seen_at` is what makes the settle delay possible without a scheduler.
    The row is created the moment a condition is first observed, but nothing is
    raised until it has still been there `settle_minutes` later, which is how a
    jam cleared in two minutes stays silent.

    Deleted when the condition clears, so the next occurrence is a new
    transition and does get reported.
    """

    __tablename__ = "notification_states"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # "printer.error:<printer uuid>:media-jam". Unique: the whole mechanism is
    # that a second observation of the same condition cannot create a second
    # row, and that is enforced by the database rather than by a check that
    # races with the next poll cycle.
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String)

    subject_type: Mapped[str | None] = mapped_column(String, default=None)
    subject_id: Mapped[str | None] = mapped_column(String, default=None)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Null while the condition is still inside its settle window. Set once an
    # event has been raised, so a long-running condition is not raised again on
    # every subsequent pass.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class NotificationEvent(Base):
    """Something worth telling somebody, and whether it got there.

    Append-only, and written in the transaction that detected the condition —
    the same property app/audit/record.py rests on. If the status change that
    triggered it rolls back, the alert goes with it: no message about a state
    that never persisted, and none missed for one that did.

    `title` and `body` are rendered at raise time rather than at delivery.
    A message describing what was wrong is worth more than one describing what
    is wrong now, which by the time a retry succeeds may be nothing.
    """

    __tablename__ = "notification_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    kind: Mapped[str] = mapped_column(String, index=True)
    dedupe_key: Mapped[str] = mapped_column(String, index=True)

    subject_type: Mapped[str | None] = mapped_column(String, default=None)
    subject_id: Mapped[str | None] = mapped_column(String, default=None)

    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(String)
    # "info" | "warning" — no "critical" yet, because nothing here is, and a
    # severity nobody uses trains people to ignore the ones that exist.
    severity: Mapped[str] = mapped_column(String, default="warning", server_default="warning")

    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # When the next attempt is allowed. Backoff lives on the row rather than in
    # the loop so a restart does not reset every failing delivery to "try now"
    # and hammer a channel that is already down.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    last_error: Mapped[str | None] = mapped_column(String, default=None)
