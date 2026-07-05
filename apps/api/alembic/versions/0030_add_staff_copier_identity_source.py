"""add source column to staff_copier_identities

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-05 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0030'
down_revision: Union[str, Sequence[str], None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'staff_copier_identities',
        sa.Column('source', sa.String(), server_default='manual', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('staff_copier_identities', 'source')
