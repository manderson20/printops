"""record what admins change

Revision ID: 0080
Revises: 0079
Create Date: 2026-09-06 18:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0080"
down_revision: Union[str, Sequence[str], None] = "0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Nothing in PrintOps has recorded who changed what. A printer's address, a
    quota, a user's role, the SNMP community string, the cost-per-page every
    chargeback figure is derived from — all editable by any admin, none of it
    leaving a trace beyond the changed row. The only way to answer "who turned
    this on" has been to ask people.

    `audit_events` is append-only and is written inside the transaction that
    makes the change it describes (app/audit/record.py), so an event cannot
    exist without its change or a change without its event.

    Two column choices are load-bearing rather than incidental:

    `actor_user_id` is nullable with ON DELETE SET NULL. Nullable because the
    dev break-glass admin has no `users` row at all, so there is nothing to
    point a foreign key at — the same situation `impersonation_sessions`
    handles. SET NULL rather than CASCADE because deleting an admin must not
    delete the evidence of what they did; `impersonation_sessions` uses CASCADE
    and is wrong about this, which is worth fixing separately. `actor_email` is
    denormalized and always populated, so a row stays readable after the
    account behind it is renamed or gone.

    `changes` is JSON rather than a pair of text columns because most actions
    touch several fields at once and one row per field would make a single
    settings save read as forty entries.

    Indexes on `occurred_at` and `action` only. Those are the two axes the UI
    actually filters on; an index per column on an append-only table costs
    write throughput on every admin action for queries nobody runs.
    """
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email", sa.String(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("impersonated_by", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=True),
        sa.Column("entity_id", sa.String(), nullable=True),
        sa.Column("entity_label", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("source_ip", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"])
    op.create_index(op.f("ix_audit_events_actor_user_id"), "audit_events", ["actor_user_id"])
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])

    # Singleton, created lazily by the API on first read like every other
    # settings table here — no seed row inserted, so this migration does not
    # need a timestamp default written by hand. 0073 did that with
    # sa.func.now() as a bind parameter and asyncpg rejected it mid-deploy,
    # rolling back the whole upgrade; not repeating it.
    op.create_table(
        "audit_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retention_days", sa.Integer(), server_default="400", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the trail. There is nowhere to preserve it to — that is the nature of
    removing the table that is the record — so a downgrade past this point
    genuinely loses the history, which is worth knowing before running it on
    anything that has been recording for a while.
    """
    op.drop_table("audit_settings")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_actor_user_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_table("audit_events")
