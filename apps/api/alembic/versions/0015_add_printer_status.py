"""add printer status

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-03 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('printers', sa.Column('status', sa.String(), server_default='unknown', nullable=False))
    op.add_column('printers', sa.Column('status_reasons', sa.JSON(), nullable=True))
    op.add_column('printers', sa.Column('status_message', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('status_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printers', 'status_checked_at')
    op.drop_column('printers', 'status_message')
    op.drop_column('printers', 'status_reasons')
    op.drop_column('printers', 'status')
