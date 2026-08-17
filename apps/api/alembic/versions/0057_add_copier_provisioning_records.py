"""add copier provisioning records and sync jobs

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0057'
down_revision: Union[str, Sequence[str], None] = '0056'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    copier_provisioned_accounts records which person PrintOps put on which
    copier. The device cannot tell us: a bizhub reports only whether an
    account has a password, never what it is, so without this a re-sync
    re-adds everyone and every one is rejected as a duplicate.

    copier_sync_jobs makes a sync observable while it runs — one HTTP round
    trip per account against a single-session device takes minutes, and a
    spinner cannot distinguish that from a hang.
    """
    op.create_table(
        'copier_provisioned_accounts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=False),
        sa.Column('staff_email', sa.String(), nullable=False),
        sa.Column('identity_value', sa.String(), nullable=False),
        sa.Column('identity_type', sa.String(), server_default='staff_id', nullable=False),
        sa.Column('device_account_id', sa.String(), nullable=False),
        sa.Column('device_account_name', sa.String(), nullable=True),
        sa.Column('provisioned_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copier_provisioned_accounts_mfp_device_id', 'copier_provisioned_accounts', ['mfp_device_id'])
    op.create_index('ix_copier_provisioned_accounts_staff_email', 'copier_provisioned_accounts', ['staff_email'])
    op.create_index('ix_copier_provisioned_accounts_identity_value', 'copier_provisioned_accounts', ['identity_value'])
    op.create_index('ix_copier_provisioned_accounts_removed_at', 'copier_provisioned_accounts', ['removed_at'])
    op.create_index('ix_copier_provisioned_device_identity', 'copier_provisioned_accounts', ['mfp_device_id', 'identity_value'])
    op.create_index('ix_copier_provisioned_device_account', 'copier_provisioned_accounts', ['mfp_device_id', 'device_account_id'])

    op.create_table(
        'copier_sync_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(), server_default='pending', nullable=False),
        sa.Column('trigger', sa.String(), server_default='manual', nullable=False),
        sa.Column('total', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('skipped', sa.Integer(), server_default='0', nullable=False),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copier_sync_jobs_mfp_device_id', 'copier_sync_jobs', ['mfp_device_id'])
    op.create_index('ix_copier_sync_jobs_status', 'copier_sync_jobs', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('copier_sync_jobs')
    op.drop_table('copier_provisioned_accounts')
