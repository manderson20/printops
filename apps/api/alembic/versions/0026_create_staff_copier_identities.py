"""create staff_copier_identities

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-04 17:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0026'
down_revision: Union[str, Sequence[str], None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'staff_copier_identities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('staff_email', sa.String(), nullable=False),
        sa.Column('identity_type', sa.String(), nullable=False),
        sa.Column('identity_value', sa.String(), nullable=False),
        sa.Column('mfp_device_id', sa.Uuid(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mfp_device_id'], ['mfp_devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_staff_copier_identities_staff_email', 'staff_copier_identities', ['staff_email'])
    op.create_index('ix_staff_copier_identities_identity_type', 'staff_copier_identities', ['identity_type'])
    op.create_index('ix_staff_copier_identities_identity_value', 'staff_copier_identities', ['identity_value'])
    op.create_index('ix_staff_copier_identities_mfp_device_id', 'staff_copier_identities', ['mfp_device_id'])
    op.create_index(
        'ix_staff_copier_identities_type_value',
        'staff_copier_identities',
        ['identity_type', 'identity_value'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_staff_copier_identities_type_value', 'staff_copier_identities')
    op.drop_index('ix_staff_copier_identities_mfp_device_id', 'staff_copier_identities')
    op.drop_index('ix_staff_copier_identities_identity_value', 'staff_copier_identities')
    op.drop_index('ix_staff_copier_identities_identity_type', 'staff_copier_identities')
    op.drop_index('ix_staff_copier_identities_staff_email', 'staff_copier_identities')
    op.drop_table('staff_copier_identities')
