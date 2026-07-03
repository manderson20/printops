"""add printer counter readings history + retention_days

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-03 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'snmp_defaults_settings',
        sa.Column('retention_days', sa.Integer(), server_default='180', nullable=False),
    )

    op.create_table(
        'printer_counter_readings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('page_count_total', sa.Integer(), nullable=True),
        sa.Column('page_count_copy', sa.Integer(), nullable=True),
        sa.Column('page_count_print', sa.Integer(), nullable=True),
        sa.Column('page_count_confidence', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_printer_counter_readings_printer_id', 'printer_counter_readings', ['printer_id']
    )
    op.create_index(
        'ix_printer_counter_readings_printer_recorded',
        'printer_counter_readings',
        ['printer_id', 'recorded_at'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printer_counter_readings_printer_recorded', 'printer_counter_readings')
    op.drop_index('ix_printer_counter_readings_printer_id', 'printer_counter_readings')
    op.drop_table('printer_counter_readings')

    op.drop_column('snmp_defaults_settings', 'retention_days')
