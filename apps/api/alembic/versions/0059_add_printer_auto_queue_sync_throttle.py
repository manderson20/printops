"""add printer automatic queue sync throttle

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0059'
down_revision: Union[str, Sequence[str], None] = '0058'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Inputs to the automatic-queue-resync cooldown (app/printers/status.py).
    A resync holds a cupsd client slot for the whole of `lpadmin -m
    everywhere`'s 30-second timeout when a device answers slowly, so one
    flapping printer resyncing every few minutes can exhaust MaxClients and
    stall every print client on the server. These record when PrintOps last
    resynced a queue by itself and how many of those failed in a row, which
    is what the backoff is computed from.
    """
    op.add_column('printers', sa.Column('last_auto_queue_sync_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('printers', sa.Column('auto_queue_sync_failures', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('printers', 'auto_queue_sync_failures')
    op.drop_column('printers', 'last_auto_queue_sync_at')
