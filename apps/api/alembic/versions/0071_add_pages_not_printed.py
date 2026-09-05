"""record pages a printer was sent but never printed

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-04 20:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: Union[str, Sequence[str], None] = "0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A printer can accept a job, report it completed successfully, and print
    nothing at all — an HP LaserJet M607 did it to the same PDF three times
    between 2026-09-03 and 2026-09-04 while every record PrintOps and CUPS keep
    said the job had printed. The only witness is the device's own page
    counter, which this server already samples every thirty minutes and has
    never compared against what it sent (app/printers/page_reconcile.py).

    This holds the standing verdict of that comparison so the 60s status poll
    can render it. The poll rebuilds status_reasons from nothing every cycle,
    so it cannot also be the thing that computes a half-hourly question asked
    of two tables — the same split, and for the same reason, as the network
    probe log in migration 0063.

    Nullable with no default, and it means "nothing to report" rather than
    "this printer is fine": a device with no readable page counter can never
    be assessed at all, and 36 of the 54 printers on this fleet report only a
    combined total. Reading null as a clean bill of health would turn the
    printers this can say least about into the ones that look safest.
    """
    op.add_column("printers", sa.Column("pages_not_printed", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "pages_not_printed")
