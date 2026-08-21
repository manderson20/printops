"""split auto-detected ipp path from the admin override

Revision ID: 0061
Revises: 0060
Create Date: 2026-08-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: Union[str, Sequence[str], None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    `printers.ipp_path` has been doing two jobs at once: an admin's deliberate
    choice, and a cache of whatever the probe last discovered
    (app/printers/discovery.py wrote its result straight into the same column).
    Nothing recorded which was which, and the two need opposite treatment — an
    override must survive, a cached guess must be refreshable when the device
    changes underneath it.

    That ambiguity has a cost. The LCACTC Kyocera sat on '/' — almost certainly
    auto-detected back when it answered plain IPP on 631 — and because the
    value was *stored*, discovery never revisited it after the device was moved
    to TLS-only. A cache that cannot be invalidated is just a stale guess with
    tenure.

    Existing values move into the cache column rather than staying as
    overrides, so the whole fleet becomes self-healing (an explicit decision;
    the alternative was to keep them pinned and leave the staleness in place).
    The trade-off is that a path someone genuinely typed by hand is now
    re-detectable and could be replaced by what probing finds. That is
    acceptable here because probing only ever writes a path that actually
    answered, and because an admin who wants one pinned can type it again — it
    then lands in `ipp_path` and is never touched.
    """
    op.add_column("printers", sa.Column("ipp_path_detected", sa.String(), nullable=True))
    # Empty strings are treated as "not set" everywhere in the app, so they are
    # normalised to NULL here rather than carried into the cache as a value
    # that would suppress detection forever.
    op.execute(
        """
        UPDATE printers
           SET ipp_path_detected = NULLIF(ipp_path, ''),
               ipp_path = NULL
         WHERE ipp_path IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema.

    Folds the cache back into the single column. An override wins over a cached
    value, matching how the application resolves the two (Printer.
    effective_ipp_path) — so downgrading cannot silently point a printer at a
    path its admin had overridden.
    """
    op.execute(
        """
        UPDATE printers
           SET ipp_path = COALESCE(NULLIF(ipp_path, ''), ipp_path_detected)
         WHERE ipp_path IS NULL OR ipp_path = ''
        """
    )
    op.drop_column("printers", "ipp_path_detected")
