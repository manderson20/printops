"""add printer quota_mode and nullable page_limit

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0053'
down_revision: Union[str, Sequence[str], None] = '0052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # "include" preserves the pre-existing behaviour exactly (only users with
    # their own row are capped), so any printer configured before this
    # migration keeps enforcing what its admin originally set up.
    op.add_column(
        'printers',
        sa.Column(
            'quota_mode',
            sa.String(),
            nullable=False,
            server_default='include',
        ),
    )
    # NULL page_limit = "no cap" — how an exclude-mode exemption row is
    # stored, since an exemption names a user without giving them a number.
    op.alter_column('printer_user_quotas', 'page_limit', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Exemption rows carry no limit and have no meaning without quota_mode, so
    # they'd violate the restored NOT NULL. Drop them rather than inventing a
    # page_limit that would silently start capping the very people who were
    # explicitly let out.
    op.execute("DELETE FROM printer_user_quotas WHERE page_limit IS NULL")
    op.alter_column('printer_user_quotas', 'page_limit', existing_type=sa.Integer(), nullable=False)
    op.drop_column('printers', 'quota_mode')
