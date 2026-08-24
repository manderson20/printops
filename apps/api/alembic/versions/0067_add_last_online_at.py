"""remember when a printer was last reachable

Revision ID: 0067
Revises: 0066
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, Sequence[str], None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    PrintOps knew a printer was offline and when it last *checked*, but not
    when it was last actually reachable — and those answer different questions.
    "Offline" looks identical whether a printer was switched off an hour ago or
    has never once answered because its stored address belongs to nothing.

    On 2026-08-23 a teacher's job sat waiting for ES 4th Grade Printer, which
    everyone (including me) took to be switched off for the night. It was on the
    whole time: the record said 10.20.3.104 and the printer was at 10.10.1.10.
    Nothing could distinguish the two cases, so nothing did, and the job waited
    a day for a printer that was never going to be reached at that address.

    With this, a printer that has never been online, or has not been online for
    a long time, can be told apart from one that is simply away — see
    app/printers/offline_holds.py:waiting_alert.
    """
    op.add_column(
        "printers", sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Seeded, not left null. Null means "has never answered", which is the
    # loudest thing this column can say — and on the upgrade itself every
    # printer that ever worked would say it. A working printer that happened to
    # be switched off during the deploy, with a job waiting, would be reported
    # as a wrong address on the very next poll instead of getting the day's
    # grace it has earned.
    #
    # status_checked_at is the closest thing to the truth already on the row;
    # where there is none, the upgrade itself is the conservative answer. Either
    # way a printer starts from "seen recently" and has to go quiet for a day
    # before anything is said about it.
    op.execute("UPDATE printers SET last_online_at = COALESCE(status_checked_at, NOW())")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("printers", "last_online_at")
