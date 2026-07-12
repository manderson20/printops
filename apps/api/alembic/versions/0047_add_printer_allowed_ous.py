"""add printer_allowed_ous

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-12 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0047'
down_revision: Union[str, Sequence[str], None] = '0046'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'printer_allowed_ous',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('ou_path', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('printer_id', 'ou_path', name='uq_printer_allowed_ou_path'),
    )
    op.create_index(
        'ix_printer_allowed_ous_printer_id', 'printer_allowed_ous', ['printer_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printer_allowed_ous_printer_id', table_name='printer_allowed_ous')
    op.drop_table('printer_allowed_ous')
