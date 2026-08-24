"""make a copier a feature of a printer rather than a separate thing

Revision ID: 0068
Revises: 0067
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: Union[str, Sequence[str], None] = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A copier and a printer were two records for one machine, in two places in
    the UI, each describing the same connection, model, location and meter. An
    admin had to know a given machine appeared twice, and which page answered
    which question.

    Every copier is a printer, so the printer row is now the machine and the
    copier is a capability it can be given. `mfp_devices` keeps everything
    it holds — the accounting data hangs off `mfp_device_id` and every foreign
    key into that table is ON DELETE CASCADE, so 1620 provisioned accounts,
    1735 counter readings and 121 usage records ride on that row continuing to
    exist. Turning the copier feature off therefore only clears this flag; it
    never deletes the device.

    Backfilled true for every printer that already has a copier linked to it,
    so nothing an admin already set up changes or disappears.
    """
    op.add_column(
        "printers",
        sa.Column("copier_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        """
        UPDATE printers
        SET copier_enabled = true
        WHERE id IN (SELECT printer_id FROM mfp_devices WHERE printer_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "copier_enabled")
