"""add snmp counter polling

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-03 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0023'
down_revision: Union[str, Sequence[str], None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'printers',
        sa.Column('snmp_enabled', sa.Boolean(), server_default='true', nullable=False),
    )
    op.add_column('printers', sa.Column('snmp_port', sa.Integer(), nullable=True))
    op.add_column('printers', sa.Column('snmp_version', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('snmp_community_encrypted', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('snmp_vendor_profile', sa.String(), nullable=True))

    op.add_column('printers', sa.Column('page_count_total', sa.Integer(), nullable=True))
    op.add_column('printers', sa.Column('page_count_copy', sa.Integer(), nullable=True))
    op.add_column('printers', sa.Column('page_count_print', sa.Integer(), nullable=True))
    op.add_column('printers', sa.Column('page_count_confidence', sa.String(), nullable=True))
    op.add_column('printers', sa.Column('page_count_vendor_profile_used', sa.String(), nullable=True))
    op.add_column(
        'printers', sa.Column('page_count_checked_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('printers', sa.Column('page_count_error', sa.String(), nullable=True))

    op.create_table(
        'snmp_defaults_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('community_encrypted', sa.String(), nullable=True),
        sa.Column('version', sa.String(), server_default='v2c', nullable=False),
        sa.Column('port', sa.Integer(), server_default='161', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('snmp_defaults_settings')

    op.drop_column('printers', 'page_count_error')
    op.drop_column('printers', 'page_count_checked_at')
    op.drop_column('printers', 'page_count_vendor_profile_used')
    op.drop_column('printers', 'page_count_confidence')
    op.drop_column('printers', 'page_count_print')
    op.drop_column('printers', 'page_count_copy')
    op.drop_column('printers', 'page_count_total')

    op.drop_column('printers', 'snmp_vendor_profile')
    op.drop_column('printers', 'snmp_community_encrypted')
    op.drop_column('printers', 'snmp_version')
    op.drop_column('printers', 'snmp_port')
    op.drop_column('printers', 'snmp_enabled')
