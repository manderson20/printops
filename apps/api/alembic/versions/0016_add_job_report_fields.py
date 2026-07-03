"""add job report fields

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-04 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('document_name', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('copy_count', sa.Integer(), nullable=True))
    op.add_column('jobs', sa.Column('color_mode', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('duplex', sa.Boolean(), nullable=True))
    op.add_column('jobs', sa.Column('paper_size', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('source', sa.String(), server_default='cups', nullable=False))
    op.add_column('jobs', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))

    op.create_index('ix_jobs_status', 'jobs', ['status'])
    op.create_index('ix_jobs_submitted_by', 'jobs', ['submitted_by'])
    op.create_index('ix_printers_building', 'printers', ['building'])
    op.create_index('ix_printers_department', 'printers', ['department'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printers_department', table_name='printers')
    op.drop_index('ix_printers_building', table_name='printers')
    op.drop_index('ix_jobs_submitted_by', table_name='jobs')
    op.drop_index('ix_jobs_status', table_name='jobs')

    op.drop_column('jobs', 'completed_at')
    op.drop_column('jobs', 'source')
    op.drop_column('jobs', 'paper_size')
    op.drop_column('jobs', 'duplex')
    op.drop_column('jobs', 'color_mode')
    op.drop_column('jobs', 'copy_count')
    op.drop_column('jobs', 'document_name')
