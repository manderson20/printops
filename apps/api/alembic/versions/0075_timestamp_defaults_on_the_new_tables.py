"""give the road-trip tables the timestamp defaults every other table has

Revision ID: 0075
Revises: 0074
Create Date: 2026-09-05 20:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: Union[str, Sequence[str], None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("locations", "destinations", "road_trip_settings")


def upgrade() -> None:
    """Upgrade schema.

    0072 and 0073 created these three tables by hand and declared
    created_at and updated_at NOT NULL without the `server_default=now()`
    that every table since 0001 has carried. TimestampMixin sets no
    Python-side default — it relies entirely on the database — so any
    insert that did not spell the timestamps out itself failed with

        null value in column "created_at" violates not-null constraint

    The seeded rows in those migrations passed their own timestamps, so
    the tables looked healthy. The first insert that did not was adding
    suggested places, which is where it surfaced: a 500 on the one action
    the feature exists for.

    The tests did not catch it because they never run migrations. A test
    database is built from the models with `Base.metadata.create_all`,
    which applies the server_default declared on TimestampMixin;
    production's schema comes from these files. The two disagreed, and
    only the one nobody tested was wrong.
    """
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table,
                column,
                server_default=sa.text("now()"),
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Downgrade schema.

    Puts back the state that could not accept an insert. Only meaningful
    as part of unwinding the road trip entirely.
    """
    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table,
                column,
                server_default=None,
                existing_type=sa.DateTime(timezone=True),
                existing_nullable=False,
            )
