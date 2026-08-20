"""add copier account counter readings

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0058'
down_revision: Union[str, Sequence[str], None] = '0057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    copier_account_counter_readings stores what a copier's per-account
    counters said the last time they were read. The counters are lifetime
    running totals — read once, they report an account's whole history —
    so usage only exists as the difference between two readings, and this
    is what that difference is taken against.

    Rows are run-length encoded (first_seen_at/last_seen_at, one live row
    per account with superseded_at null) so an idle account costs one row
    rather than one per poll, and so a usage window can start at the last
    poll that saw the old values instead of at the day they appeared.
    """
    op.create_table(
        'copier_account_counter_readings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(), server_default='default', nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=False),
        sa.Column('device_account_id', sa.String(), nullable=False),
        sa.Column('counters', sa.JSON(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('follows_counter_reset', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copier_account_counter_readings_tenant_id', 'copier_account_counter_readings', ['tenant_id'])
    op.create_index('ix_copier_account_counter_readings_mfp_device_id', 'copier_account_counter_readings', ['mfp_device_id'])
    op.create_index('ix_copier_account_counter_readings_device_account_id', 'copier_account_counter_readings', ['device_account_id'])
    op.create_index('ix_copier_account_counter_readings_superseded_at', 'copier_account_counter_readings', ['superseded_at'])
    op.create_index('ix_copier_counter_readings_current', 'copier_account_counter_readings', ['mfp_device_id', 'device_account_id', 'superseded_at'])

    op.add_column('mfp_devices', sa.Column('auto_poll_counters', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('mfp_devices', sa.Column('last_counter_poll_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('mfp_devices', sa.Column('last_counter_poll_ok', sa.Boolean(), nullable=True))
    op.add_column('mfp_devices', sa.Column('last_counter_poll_message', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('mfp_devices', 'last_counter_poll_message')
    op.drop_column('mfp_devices', 'last_counter_poll_ok')
    op.drop_column('mfp_devices', 'last_counter_poll_at')
    op.drop_column('mfp_devices', 'auto_poll_counters')
    op.drop_table('copier_account_counter_readings')
