"""create report_formula_settings

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-04 03:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'report_formula_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('cost_per_page_mono', sa.Float(), server_default='0.03', nullable=False),
        sa.Column('cost_per_page_color', sa.Float(), server_default='0.10', nullable=False),
        sa.Column('sheets_per_tree', sa.Float(), server_default='8333.0', nullable=False),
        sa.Column('co2_grams_per_sheet', sa.Float(), server_default='4.6', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('report_formula_settings')
