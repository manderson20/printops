"""create copier_usage_records

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-04 17:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0028'
down_revision: Union[str, Sequence[str], None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'copier_usage_records',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(), server_default='default', nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=False),
        sa.Column('vendor', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('serial_number', sa.String(), nullable=True),
        sa.Column('location_building', sa.String(), nullable=True),
        sa.Column('staff_email', sa.String(), nullable=True),
        sa.Column('staff_employee_id', sa.String(), nullable=True),
        sa.Column('external_identity_used', sa.String(), nullable=False),
        sa.Column('external_identity_type', sa.String(), nullable=True),
        sa.Column('authentication_method', sa.String(), nullable=True),
        sa.Column('activity_type', sa.String(), server_default='copy', nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=True),
        sa.Column('sheet_count', sa.Integer(), nullable=True),
        sa.Column('color_page_count', sa.Integer(), nullable=True),
        sa.Column('monochrome_page_count', sa.Integer(), nullable=True),
        sa.Column('duplex', sa.Boolean(), nullable=True),
        sa.Column('paper_size', sa.String(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_connector', sa.String(), nullable=False),
        sa.Column('import_batch_id', sa.Uuid(), nullable=True),
        sa.Column('raw_payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['import_batch_id'], ['copier_import_batches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copier_usage_records_tenant_id', 'copier_usage_records', ['tenant_id'])
    op.create_index('ix_copier_usage_records_mfp_device_id', 'copier_usage_records', ['mfp_device_id'])
    op.create_index('ix_copier_usage_records_staff_email', 'copier_usage_records', ['staff_email'])
    op.create_index(
        'ix_copier_usage_records_external_identity_used', 'copier_usage_records', ['external_identity_used']
    )
    op.create_index(
        'ix_copier_usage_records_external_identity_type', 'copier_usage_records', ['external_identity_type']
    )
    op.create_index('ix_copier_usage_records_import_batch_id', 'copier_usage_records', ['import_batch_id'])
    op.create_index(
        'ix_copier_usage_records_device_occurred',
        'copier_usage_records',
        ['mfp_device_id', 'occurred_at'],
    )
    op.create_index(
        'ix_copier_usage_records_identity',
        'copier_usage_records',
        ['external_identity_type', 'external_identity_used'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_copier_usage_records_identity', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_device_occurred', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_import_batch_id', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_external_identity_type', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_external_identity_used', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_staff_email', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_mfp_device_id', 'copier_usage_records')
    op.drop_index('ix_copier_usage_records_tenant_id', 'copier_usage_records')
    op.drop_table('copier_usage_records')
