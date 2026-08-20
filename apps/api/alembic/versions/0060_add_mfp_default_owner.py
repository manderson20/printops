"""add mfp device default copy owner

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0060'
down_revision: Union[str, Sequence[str], None] = '0059'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Names one person who owns every copy made on a device that has no
    per-user accounting (app/copiers/device_owner.py). The watermark
    column is what keeps a lifetime meter from being attributed twice —
    both nullable with no server default, since "no owner" is and stays
    the norm for the rest of the fleet.
    """
    op.add_column('mfp_devices', sa.Column('default_owner_email', sa.String(), nullable=True))
    op.add_column(
        'mfp_devices',
        sa.Column('default_owner_attributed_through', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mfp_devices', 'default_owner_attributed_through')
    op.drop_column('mfp_devices', 'default_owner_email')
