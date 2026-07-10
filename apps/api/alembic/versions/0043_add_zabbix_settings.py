"""create zabbix settings table

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-10 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0043'
down_revision: Union[str, Sequence[str], None] = '0042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('zabbix_settings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('api_token', sa.String(), nullable=True),
    sa.Column('base_url', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('api_token')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('zabbix_settings')
