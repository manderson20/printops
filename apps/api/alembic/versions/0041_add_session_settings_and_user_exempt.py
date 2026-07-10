"""add session settings and user exempt_from_timeout

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0041'
down_revision: Union[str, Sequence[str], None] = '0040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'session_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('idle_timeout_minutes', sa.Integer(), server_default='60', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.add_column(
        'users', sa.Column('exempt_from_timeout', sa.Boolean(), server_default='false', nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'exempt_from_timeout')
    op.drop_table('session_settings')
