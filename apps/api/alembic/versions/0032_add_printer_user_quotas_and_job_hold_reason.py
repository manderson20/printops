"""add printer_user_quotas, quota_settings, and jobs.hold_reason

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0032'
down_revision: Union[str, Sequence[str], None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'printer_user_quotas',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=False),
        sa.Column('user_email', sa.String(), nullable=True),
        sa.Column('period', sa.String(), nullable=False),
        sa.Column('page_limit', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('printer_id', 'user_email', name='uq_printer_user_quota_email'),
    )
    op.create_index(
        'ix_printer_user_quotas_printer_id', 'printer_user_quotas', ['printer_id']
    )
    # At most one default/wildcard row (user_email IS NULL) per printer —
    # the plain UniqueConstraint above doesn't cover this since SQL NULL
    # never equals NULL, so two NULL rows for the same printer_id wouldn't
    # violate it without a partial index.
    op.create_index(
        'uq_printer_user_quotas_default',
        'printer_user_quotas',
        ['printer_id'],
        unique=True,
        postgresql_where=sa.text('user_email IS NULL'),
        sqlite_where=sa.text('user_email IS NULL'),
    )

    op.create_table(
        'quota_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.add_column('jobs', sa.Column('hold_reason', sa.String(), nullable=True))
    # Backfill: every existing held job was held for the only reason that
    # existed before this migration (Printer.release_required + PIN kiosk).
    op.execute("UPDATE jobs SET hold_reason = 'pin_release' WHERE status = 'held'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'hold_reason')
    op.drop_table('quota_settings')
    op.drop_index('uq_printer_user_quotas_default', table_name='printer_user_quotas')
    op.drop_index('ix_printer_user_quotas_printer_id', table_name='printer_user_quotas')
    op.drop_table('printer_user_quotas')
