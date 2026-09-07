"""tell somebody

Revision ID: 0081
Revises: 0080
Create Date: 2026-09-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: Union[str, Sequence[str], None] = "0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    PrintOps notices things and tells nobody. A printer whose address is wrong
    takes jobs into a queue that will never drain; a cartridge runs to empty; a
    person's job is held on quota. All of it is detected and recorded, and none
    of it travels.

    Four tables, and the interesting one is `notification_states`.

    `notification_events` is the outbox: append-only, written in the
    transaction that detected the condition, drained by a delivery loop. Same
    shape and the same reason as `audit_events` — an alert about a status that
    rolled back would be a lie.

    `notification_states` is the dedupe, and it is what makes the feature
    usable rather than muted. A printer offline for three days is *one
    condition* observed by 4,320 poll cycles, and must produce one message.
    So the unique constraint on `dedupe_key` is doing real work: it is what
    stops a second observation creating a second row, including when two loops
    race. `first_seen_at` carries the settle delay — a condition has to still
    be there some minutes later before anybody is told, which is what keeps a
    jam somebody clears in two minutes silent.

    `notification_channels` holds destinations. `target` is an incoming-webhook
    URL, which is a bearer credential in URL clothing: it is never returned by
    the API, and the audit trail records that it changed without recording what
    to.

    Settings and channels are created lazily by the API on first read, so
    nothing is seeded here — 0073 wrote a timestamp with sa.func.now() as a
    bind parameter, asyncpg rejected it mid-deploy and rolled back the whole
    upgrade. Not repeating that.
    """
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("settle_minutes", sa.Integer(), server_default="10", nullable=False),
        sa.Column("toner_low_percent", sa.Integer(), server_default="15", nullable=False),
        sa.Column(
            "notify_printer_attention", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("notify_printer_error", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_toner_low", sa.Boolean(), server_default="true", nullable=False),
        # The one trigger defaulting off. It is the noisiest and the least
        # clearly an admin's business — somebody hitting their quota is between
        # them and their quota until a district decides otherwise.
        sa.Column("notify_quota_exceeded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), server_default="webhook", nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notification_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notification_states_dedupe_key"),
        "notification_states",
        ["dedupe_key"],
        unique=True,
    )

    op.create_table(
        "notification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), server_default="warning", nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_events_created_at"), "notification_events", ["created_at"])
    op.create_index(op.f("ix_notification_events_kind"), "notification_events", ["kind"])
    op.create_index(op.f("ix_notification_events_dedupe_key"), "notification_events", ["dedupe_key"])
    # The delivery loop's own query: undelivered rows whose next attempt is due.
    op.create_index(
        op.f("ix_notification_events_next_attempt_at"), "notification_events", ["next_attempt_at"]
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the configured channels with everything else, so a downgrade and
    re-upgrade means pasting the webhook URLs in again. Nothing else depends on
    these tables.
    """
    op.drop_index(op.f("ix_notification_events_next_attempt_at"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_dedupe_key"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_kind"), table_name="notification_events")
    op.drop_index(op.f("ix_notification_events_created_at"), table_name="notification_events")
    op.drop_table("notification_events")
    op.drop_index(op.f("ix_notification_states_dedupe_key"), table_name="notification_states")
    op.drop_table("notification_states")
    op.drop_table("notification_channels")
    op.drop_table("notification_settings")
