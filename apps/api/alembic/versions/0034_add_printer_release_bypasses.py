"""add printer_release_bypasses

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-09 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0034'
down_revision: Union[str, Sequence[str], None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'printer_release_bypasses',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('user_email', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('printer_id', 'user_email', name='uq_printer_release_bypass_email'),
    )
    op.create_index(
        'ix_printer_release_bypasses_printer_id', 'printer_release_bypasses', ['printer_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printer_release_bypasses_printer_id', table_name='printer_release_bypasses')
    op.drop_table('printer_release_bypasses')
