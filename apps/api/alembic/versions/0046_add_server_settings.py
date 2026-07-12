"""create server settings table

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0046'
down_revision: Union[str, Sequence[str], None] = '0045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('server_settings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('hostname', sa.String(), server_default='', nullable=False),
    sa.Column('require_encryption', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('advertise_ipps', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('sync_error', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('server_settings')
