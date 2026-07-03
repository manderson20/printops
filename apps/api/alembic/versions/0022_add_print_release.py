"""add print-and-release fields

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-03 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0022'
down_revision: Union[str, Sequence[str], None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printers',
        sa.Column('release_required', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column('printers', sa.Column('release_token', sa.String(), nullable=True))
    op.create_unique_constraint('uq_printers_release_token', 'printers', ['release_token'])

    op.add_column('jobs', sa.Column('held_file_path', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('held_job_options', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('held_expires_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'print_release_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('hold_expiry_hours', sa.Float(), server_default='4.0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('print_release_settings')

    op.drop_column('jobs', 'held_expires_at')
    op.drop_column('jobs', 'held_job_options')
    op.drop_column('jobs', 'held_file_path')

    op.drop_constraint('uq_printers_release_token', 'printers', type_='unique')
    op.drop_column('printers', 'release_token')
    op.drop_column('printers', 'release_required')
