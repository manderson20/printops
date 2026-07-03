"""add printer toner cartridges

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-04 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0019'
down_revision: Union[str, Sequence[str], None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'report_formula_settings',
        sa.Column('cost_per_sheet_paper', sa.Float(), server_default='0.01', nullable=False),
    )

    op.create_table(
        'printer_toner_cartridges',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('color', sa.String(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('yield_pages', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('printer_id', 'color', name='uq_printer_toner_color'),
    )
    op.create_index(
        'ix_printer_toner_cartridges_printer_id', 'printer_toner_cartridges', ['printer_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printer_toner_cartridges_printer_id', table_name='printer_toner_cartridges')
    op.drop_table('printer_toner_cartridges')
    op.drop_column('report_formula_settings', 'cost_per_sheet_paper')
