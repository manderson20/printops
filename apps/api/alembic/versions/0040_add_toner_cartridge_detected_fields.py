"""add printer toner cartridge detected fields

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0040'
down_revision: Union[str, Sequence[str], None] = '0039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printer_toner_cartridges', sa.Column('detected_description', sa.String(), nullable=True)
    )
    op.add_column(
        'printer_toner_cartridges',
        sa.Column('detected_high_capacity', sa.Boolean(), nullable=True),
    )
    op.add_column(
        'printer_toner_cartridges',
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printer_toner_cartridges', 'detected_at')
    op.drop_column('printer_toner_cartridges', 'detected_high_capacity')
    op.drop_column('printer_toner_cartridges', 'detected_description')
