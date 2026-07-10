"""add user granted_ou_paths for ou_viewer role

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-10 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0042'
down_revision: Union[str, Sequence[str], None] = '0041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users', sa.Column('granted_ou_paths', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'granted_ou_paths')
