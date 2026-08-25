"""record whether a printer is running a Wi-Fi network of its own

Revision ID: 0070
Revises: 0069
Create Date: 2026-08-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: Union[str, Sequence[str], None] = "0069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Many printers can host an access point of their own — Wi-Fi Direct on HP,
    Access Point Mode on Canon — and ship with it on. That is a way onto the
    machine that never touches the district's network: no VLAN, no ACL, no
    PrintOps. Until now the only way to find one was to walk the building
    watching the Wi-Fi list on a phone; a sweep on 2026-08-25 found six, all
    HP, each running a network nobody had asked for.

    The radio never appears on the wired network, but the device describes it
    in the standard MIB-II interface table, so this is read from the SNMP poll
    that already runs every 30 minutes (app/printers/wireless.py).

    All four columns are nullable with no default, and mean it: a printer that
    has never been polled, or that stopped answering SNMP, must read as
    "unknown" rather than as "not broadcasting". A printer that stopped
    answering is not a printer that stopped broadcasting.
    """
    op.add_column("printers", sa.Column("wireless_broadcasting", sa.Boolean(), nullable=True))
    # One entry per radio: {"name", "up", "access_point"}. Kept alongside the
    # boolean because the boolean cannot show its working, and "wifi0 down,
    # wifiUAP up" is what tells an admin which switch on the panel to look for.
    # An empty list is a real answer — this device has no wireless hardware.
    op.add_column("printers", sa.Column("wireless_radios", sa.JSON(), nullable=True))
    op.add_column(
        "printers", sa.Column("wireless_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("printers", sa.Column("wireless_error", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "wireless_error")
    op.drop_column("printers", "wireless_checked_at")
    op.drop_column("printers", "wireless_radios")
    op.drop_column("printers", "wireless_broadcasting")
