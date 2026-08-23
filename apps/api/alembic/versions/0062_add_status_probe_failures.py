"""count consecutive status probe failures, to debounce going offline

Revision ID: 0062
Revises: 0061
Create Date: 2026-08-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: Union[str, Sequence[str], None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    One failed IPP probe used to mark a printer offline. That is a stronger
    claim than a single 5-second timeout can support, and a printer on a
    lossy path breaks it routinely: the LCACTC RM 502 loses ~18% of packets
    sent to it in 7-second blackouts every 37 seconds (measured 2026-08-23,
    under continuous traffic; the device's own counters are clean, so the
    packets never arrive). The probe usually survives on TCP retransmits, and
    when the ladder overshoots the timeout the printer flips to offline and
    back — with a capability rediscovery and a queue resync on every trip.

    Counting the failures instead means a printer has to miss twice in a row
    before PrintOps says it is offline, which a genuinely absent printer does
    immediately and a sleeping one almost never does. Persisted rather than
    kept in memory so an API restart doesn't reset the count and let a single
    miss speak again, matching how auto_queue_sync_failures is kept.
    """
    op.add_column(
        "printers",
        sa.Column("status_probe_failures", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "status_probe_failures")
