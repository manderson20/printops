"""keep a typed distance where a recompute cannot erase it

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-05 21:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: Union[str, Sequence[str], None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    The settings page promised that a distance an admin types survives
    every later refresh. It did not. `miles` was overwritten from
    `route_miles` on every recompute — and a recompute runs on every add,
    delete, reorder and coordinate change — so an override lasted until
    the next time anything moved.

    The rule was never storable in the first place: with one column
    holding both the ladder's distance and the admin's intention, nothing
    could tell a deliberate 120 from a stale copy of a measured 122.6.
    `miles_override` is that intention, kept where a recompute has no
    reason to touch it.

    Backfilled from the only evidence there is: a row whose `miles`
    already disagrees with a `route_miles` it has been given was
    overridden by somebody, because nothing else could have made the two
    differ.

    A row that has never been routed is deliberately *not* treated as
    overridden. Its distance came from the ladder that used to be compiled
    into equivalency_config.py — an estimate somebody wrote once, not a
    decision an admin made — and freezing those forever would mean the
    seeded rungs never picking up their real driving distances no matter
    how many times the trip was driven. They follow the road the first
    time it is measured, which is what the button promises.

    A rung with no coordinates keeps its figure regardless: nothing
    measures it, so nothing can overwrite it, and it needs no override to
    say so.
    """
    op.add_column("destinations", sa.Column("miles_override", sa.Float(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE destinations
            SET miles_override = miles
            WHERE route_miles IS NOT NULL
              AND abs(miles - route_miles) > 0.05
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    `miles` already carries whatever the override said, so dropping the
    column loses the distinction rather than the distance — and returns to
    the behaviour where the next recompute quietly overwrites it.
    """
    op.drop_column("destinations", "miles_override")
