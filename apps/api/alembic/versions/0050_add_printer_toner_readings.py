"""add printer_toner_readings

Revision ID: 0050
Revises: 0049
Create Date: 2026-07-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0050'
down_revision: Union[str, Sequence[str], None] = '0049'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'printer_toner_readings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('color', sa.String(), nullable=False),
        sa.Column('level_percent', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_printer_toner_readings_printer_id', 'printer_toner_readings', ['printer_id']
    )
    op.create_index(
        'ix_printer_toner_readings_printer_color_recorded',
        'printer_toner_readings',
        ['printer_id', 'color', 'recorded_at'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_printer_toner_readings_printer_color_recorded', table_name='printer_toner_readings'
    )
    op.drop_index('ix_printer_toner_readings_printer_id', table_name='printer_toner_readings')
    op.drop_table('printer_toner_readings')
