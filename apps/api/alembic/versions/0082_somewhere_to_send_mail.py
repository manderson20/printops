"""somewhere to send mail

Revision ID: 0082
Revises: 0081
Create Date: 2026-09-07 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0082"
down_revision: Union[str, Sequence[str], None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    One relay, configured once. Deliberately its own table rather than columns
    on notification_settings: "Scheduled/emailed report delivery" is a separate
    item on the roadmap and wants exactly this. Notifications happen to be the
    first thing that needs to send mail.

    `password_encrypted` follows every other credential here — encrypted at
    rest through app/core/crypto.py, never returned by the API, and recorded in
    the audit trail as having changed without recording what to.

    Port 587 rather than 25: this district relays through Google Workspace, and
    25 is both blocked outbound by most ISPs and the port where nobody expects
    authentication.

    Created lazily by the API on first read, so no row is seeded here.
    """
    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("host", sa.String(), server_default="", nullable=False),
        sa.Column("port", sa.Integer(), server_default="587", nullable=False),
        sa.Column("username", sa.String(), server_default="", nullable=False),
        sa.Column("password_encrypted", sa.String(), nullable=True),
        sa.Column("from_address", sa.String(), server_default="", nullable=False),
        sa.Column("from_name", sa.String(), server_default="PrintOps", nullable=False),
        sa.Column("use_starttls", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the relay configuration, including the stored password. Any email
    notification channels survive in notification_channels and will fail until
    a relay is configured again — which is the honest outcome: the destination
    is still wanted, the means of reaching it is gone.
    """
    op.drop_table("smtp_settings")
