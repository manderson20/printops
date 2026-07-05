"""create copier_import_templates and copier_import_batches

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-04 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0027'
down_revision: Union[str, Sequence[str], None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'copier_import_templates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('vendor', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('column_mapping', sa.JSON(), nullable=False),
        sa.Column('identity_type', sa.String(), nullable=False),
        sa.Column('delimiter', sa.String(), server_default=',', nullable=False),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'copier_import_batches',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=False),
        sa.Column('template_id', sa.Uuid(), nullable=True),
        sa.Column('original_filename', sa.String(), nullable=False),
        sa.Column('raw_file_path', sa.String(), nullable=False),
        sa.Column('uploaded_by', sa.String(), nullable=False),
        sa.Column('period_label', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default='uploaded', nullable=False),
        sa.Column('column_mapping', sa.JSON(), nullable=True),
        sa.Column('identity_type', sa.String(), nullable=True),
        sa.Column('row_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('imported_row_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('duplicate_row_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('unmapped_identity_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('error_detail', sa.JSON(), nullable=True),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['copier_import_templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_copier_import_batches_mfp_device_id', 'copier_import_batches', ['mfp_device_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_copier_import_batches_mfp_device_id', 'copier_import_batches')
    op.drop_table('copier_import_batches')
    op.drop_table('copier_import_templates')
