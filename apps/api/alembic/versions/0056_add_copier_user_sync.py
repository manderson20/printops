"""add copier scheduled user sync

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0056'
down_revision: Union[str, Sequence[str], None] = '0055'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    auto_sync_users defaults to false, so applying this migration never
    starts writing accounts to any device on its own — an admin opts each
    copier in after watching a manual sync do the right thing. Pushing
    login accounts to shared hardware is not a thing to switch on for a
    whole fleet by default.
    """
    op.add_column(
        'mfp_devices',
        sa.Column('auto_sync_users', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'mfp_devices',
        sa.Column('last_user_sync_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column('mfp_devices', sa.Column('last_user_sync_ok', sa.Boolean(), nullable=True))
    op.add_column('mfp_devices', sa.Column('last_user_sync_message', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mfp_devices', 'last_user_sync_message')
    op.drop_column('mfp_devices', 'last_user_sync_ok')
    op.drop_column('mfp_devices', 'last_user_sync_at')
    op.drop_column('mfp_devices', 'auto_sync_users')
