"""create mfp_devices

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0025'
down_revision: Union[str, Sequence[str], None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'mfp_devices',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(), server_default='default', nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('vendor', sa.String(), server_default='generic', nullable=False),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('serial_number', sa.String(), nullable=True),
        sa.Column('ip_address', sa.String(), nullable=True),
        sa.Column('hostname', sa.String(), nullable=True),
        sa.Column('building', sa.String(), nullable=True),
        sa.Column('room', sa.String(), nullable=True),
        sa.Column('department', sa.String(), nullable=True),
        sa.Column('connector_type', sa.String(), server_default='generic_csv', nullable=False),
        sa.Column('connector_config', sa.JSON(), nullable=True),
        sa.Column('cap_walkup_copy_accounting', sa.Boolean(), nullable=True),
        sa.Column('cap_user_code_pin_auth', sa.Boolean(), nullable=True),
        sa.Column('cap_badge_card_auth', sa.Boolean(), nullable=True),
        sa.Column('cap_department_id_accounting', sa.Boolean(), nullable=True),
        sa.Column('cap_ldap_auth', sa.Boolean(), nullable=True),
        sa.Column('cap_local_user_table', sa.Boolean(), nullable=True),
        sa.Column('cap_remote_user_provisioning', sa.Boolean(), nullable=True),
        sa.Column('cap_csv_accounting_export', sa.Boolean(), nullable=True),
        sa.Column('cap_api_accounting_retrieval', sa.Boolean(), nullable=True),
        sa.Column('cap_snmp_meter_counters', sa.Boolean(), nullable=True),
        sa.Column('cap_scan_accounting', sa.Boolean(), nullable=True),
        sa.Column('cap_color_mono_accounting', sa.Boolean(), nullable=True),
        sa.Column('cap_quotas', sa.Boolean(), nullable=True),
        sa.Column('cap_secure_print_release', sa.Boolean(), nullable=True),
        sa.Column('capabilities_source', sa.String(), nullable=True),
        sa.Column('capabilities_detected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('snmp_enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('snmp_port', sa.Integer(), nullable=True),
        sa.Column('snmp_version', sa.String(), nullable=True),
        sa.Column('snmp_community_encrypted', sa.String(), nullable=True),
        sa.Column('snmp_vendor_profile', sa.String(), nullable=True),
        sa.Column('page_count_total', sa.Integer(), nullable=True),
        sa.Column('page_count_copy', sa.Integer(), nullable=True),
        sa.Column('page_count_print', sa.Integer(), nullable=True),
        sa.Column('page_count_confidence', sa.String(), nullable=True),
        sa.Column('page_count_vendor_profile_used', sa.String(), nullable=True),
        sa.Column('page_count_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('page_count_error', sa.String(), nullable=True),
        sa.Column('last_test_connection_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_test_connection_ok', sa.Boolean(), nullable=True),
        sa.Column('last_test_connection_message', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mfp_devices_tenant_id', 'mfp_devices', ['tenant_id'])
    op.create_index('ix_mfp_devices_printer_id', 'mfp_devices', ['printer_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_mfp_devices_printer_id', 'mfp_devices')
    op.drop_index('ix_mfp_devices_tenant_id', 'mfp_devices')
    op.drop_table('mfp_devices')
