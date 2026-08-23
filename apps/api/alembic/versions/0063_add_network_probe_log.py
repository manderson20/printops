"""keep each printer's recent status-probe outcomes across restarts

Revision ID: 0063
Revises: 0062
Create Date: 2026-08-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063"
down_revision: Union[str, Sequence[str], None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    The network-path warning (app/printers/network_health.py) is raised from
    how many status probes a printer has missed in the last hour while still
    answering in between. That history started out in a module-level dict, the
    same as the queue-stall clock, on the reasoning that a verdict this process
    did not observe is not one it can honestly report.

    That reasoning does not survive contact with this particular signal. The
    stall clock measures a duration that is still running, so a restart really
    does break the observation. A loss count is a record of probes that were
    genuinely made and genuinely went unanswered; restarting the API does not
    make them un-happen. Keeping it in memory meant every deploy silently
    cleared the evidence and gave a lossy printer a fresh hour of looking fine
    — and on a day with several deploys, the warning could never fire at all.

    So the log is persisted. It is pruned to the window on every read and
    write, so a row left behind by a printer nobody has polled for a week
    contributes nothing to a verdict.
    """
    op.add_column("printers", sa.Column("network_probe_log", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "network_probe_log")
