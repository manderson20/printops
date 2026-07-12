"""add live toner level polling + warning threshold to printer_toner_cartridges

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-12 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0049'
down_revision: Union[str, Sequence[str], None] = '0048'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printer_toner_cartridges', sa.Column('current_level_percent', sa.Integer(), nullable=True)
    )
    op.add_column(
        'printer_toner_cartridges',
        sa.Column('level_checked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'printer_toner_cartridges',
        sa.Column(
            'warning_threshold_percent', sa.Integer(), server_default='15', nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printer_toner_cartridges', 'warning_threshold_percent')
    op.drop_column('printer_toner_cartridges', 'level_checked_at')
    op.drop_column('printer_toner_cartridges', 'current_level_percent')
