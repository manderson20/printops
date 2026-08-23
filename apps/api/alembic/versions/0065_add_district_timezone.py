"""give the district a timezone, so reports are read in the hours people worked

Revision ID: 0065
Revises: 0064
Create Date: 2026-08-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: Union[str, Sequence[str], None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Timestamps are stored in UTC and should stay that way — that part was
    never the problem. What was wrong is that the reports were also *read* in
    UTC. get_peak_times counted `created_at.hour` and `.weekday()` straight off
    the stored value, so "busiest hour" was five or six hours out and anything
    printed after 7pm was attributed to the following weekday; get_timeline
    bucketed on the UTC date, so an evening's printing moved into the next
    day's total; and the CSV export wrote raw UTC ISO strings into a
    spreadsheet a human opens.

    None of that looks wrong on screen, which is what makes it worth a column
    rather than a constant: the assumption becomes visible and editable instead
    of buried in aggregation code. Defaults to America/Chicago, the district's
    own zone.
    """
    op.add_column(
        "server_settings",
        sa.Column(
            "timezone",
            sa.String(),
            nullable=False,
            server_default="America/Chicago",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("server_settings", "timezone")
