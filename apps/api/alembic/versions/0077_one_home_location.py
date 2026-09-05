"""let the database hold the district to one home

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-05 22:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: Union[str, Sequence[str], None] = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_locations_single_home"


def upgrade() -> None:
    """Upgrade schema.

    "At most one home" was enforced by the router clearing the flag from
    every other row before setting it here. Two requests racing — two
    admins, or one person clicking twice on a slow page — can each read a
    world with only the original home in it, each clear that one, and each
    set their own. The district ends with two homes, and the road trip
    reads home with `scalar_one_or_none`, so the next page load raises
    rather than picking one.

    A partial unique index makes it the database's rule instead. Postgres
    allows exactly one row where `is_home`, and the second transaction
    fails on commit rather than succeeding into an impossible state.

    The router keeps clearing the others, because that is what makes
    "Make home" work rather than fail; this is the guard underneath it,
    for the case the clear did not see.

    Any existing extra homes are collapsed first — oldest kept, which is
    the one that was there before whatever race created the second.
    """
    op.execute(
        sa.text(
            """
            UPDATE locations SET is_home = false
            WHERE is_home
              AND id <> (
                SELECT id FROM locations WHERE is_home ORDER BY created_at, id LIMIT 1
              )
            """
        )
    )
    op.create_index(
        INDEX_NAME,
        "locations",
        ["is_home"],
        unique=True,
        postgresql_where=sa.text("is_home"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Back to the router being the only thing that knows.
    """
    op.drop_index(INDEX_NAME, table_name="locations")
