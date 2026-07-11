"""add virtual follow-me queue support

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-11 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0045'
down_revision: Union[str, Sequence[str], None] = '0044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printers',
        sa.Column('is_virtual', sa.Boolean(), server_default='false', nullable=False),
    )
    op.alter_column('printers', 'ip_address', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('printers', 'ip_address', existing_type=sa.String(), nullable=False)
    op.drop_column('printers', 'is_virtual')
