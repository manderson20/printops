"""record the pages a job contained, not only the ones a printer admitted to

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-05 22:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0078"
down_revision: Union[str, Sequence[str], None] = "0077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Everything PrintOps knew about a job's size came from the printer's own
    account of what it completed. `page_count` is
    job-media-sheets-completed; job-impressions-completed, checked against
    three real jobs on the printer that motivated all this, returns the
    identical number. Both arrive after the fact and, on an IPP passthrough
    queue, both ultimately echo the device.

    That is tolerable when the device answers. Over thirty days of this
    fleet it did not answer for **881 of 7,681 forwarded jobs — 11.5%** —
    and a job with no page count is invisible to the unprinted-pages check
    (app/printers/page_reconcile.py), which is the one thing that would
    notice a printer accepting work and printing nothing. One copier
    accounted for 790 of them; the LaserJet that started this had 13 of its
    own 65.

    `submitted_pages` is the independent figure: pages counted from the
    document by the backend before delivery, times copies. Null wherever
    the count could not be taken, which is every job before this migration
    and, afterwards, the ones CUPS pipes over stdin or that are not PDF.

    Nothing is backfilled. The documents are gone.
    """
    op.add_column("jobs", sa.Column("submitted_pages", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "submitted_pages")
