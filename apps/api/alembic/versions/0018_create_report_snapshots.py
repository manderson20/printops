"""create report_snapshots

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-04 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, Sequence[str], None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'report_snapshots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('range_start', sa.Date(), nullable=False),
        sa.Column('range_end', sa.Date(), nullable=False),
        sa.Column('filters', sa.JSON(), nullable=False),
        sa.Column('totals', sa.JSON(), nullable=False),
        sa.Column('fun_facts', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_report_snapshots_created_at', 'report_snapshots', ['created_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_report_snapshots_created_at', table_name='report_snapshots')
    op.drop_table('report_snapshots')
