"""add syslog settings + printer syslog events

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0038'
down_revision: Union[str, Sequence[str], None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'syslog_settings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('port', sa.Integer(), server_default='514', nullable=False),
        sa.Column('min_severity', sa.String(), server_default='warning', nullable=False),
        sa.Column('retention_days', sa.Integer(), server_default='30', nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'printer_syslog_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('printer_id', sa.Uuid(), nullable=True),
        sa.Column('source_ip', sa.String(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('device_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('severity', sa.String(), nullable=True),
        sa.Column('facility', sa.Integer(), nullable=True),
        sa.Column('hostname', sa.String(), nullable=True),
        sa.Column('app_name', sa.String(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('raw', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['printer_id'], ['printers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_printer_syslog_events_printer_id', 'printer_syslog_events', ['printer_id']
    )
    op.create_index(
        'ix_printer_syslog_events_printer_received',
        'printer_syslog_events',
        ['printer_id', 'received_at'],
    )
    op.create_index(
        'ix_printer_syslog_events_received_at', 'printer_syslog_events', ['received_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_printer_syslog_events_received_at', 'printer_syslog_events')
    op.drop_index('ix_printer_syslog_events_printer_received', 'printer_syslog_events')
    op.drop_index('ix_printer_syslog_events_printer_id', 'printer_syslog_events')
    op.drop_table('printer_syslog_events')

    op.drop_table('syslog_settings')
