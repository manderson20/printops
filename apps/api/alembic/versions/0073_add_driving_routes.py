"""remember the drive, not just the distance

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-05 06:05:00.000000

"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: Union[str, Sequence[str], None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_ROUTING_BASE_URL = "https://router.project-osrm.org"


def upgrade() -> None:
    """Upgrade schema.

    The map drew a straight line between two pins, which is not the trip.
    Brookfield to Jefferson City is 96 miles as the crow flies and 123 by
    road, and the sentence on the dashboard claims a drive.

    So a destination now remembers the drive: how far it really is, and
    the shape of the roads that get there. Both are fetched when an admin
    saves the destination and never while a dashboard is rendering — see
    app/roadtrip/routing.py on why that distinction is the whole design.

    Nothing is backfilled. The nine seeded rungs keep the distances they
    shipped with until somebody opens Settings and asks for their routes,
    at which point the estimates are replaced by measurements. Backfilling
    here would mean a migration that makes outbound HTTP calls, which is a
    migration that can hang on a network problem and leave a database
    half-upgraded.
    """
    op.add_column("destinations", sa.Column("route_miles", sa.Float(), nullable=True))
    op.add_column("destinations", sa.Column("route_geometry", sa.JSON(), nullable=True))
    op.add_column(
        "destinations",
        sa.Column("route_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("destinations", sa.Column("route_error", sa.String(), nullable=True))

    op.create_table(
        "road_trip_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("routing_base_url", sa.String(), nullable=False),
        sa.Column("routing_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Seeded rather than created lazily so the row exists before anything
    # reads it, and so the default URL is visible in the database rather
    # than only in Python.
    now = sa.func.now()
    op.execute(
        sa.text(
            "INSERT INTO road_trip_settings "
            "(id, routing_base_url, routing_enabled, created_at, updated_at) "
            "VALUES (:id, :url, true, :created, :updated)"
        ).bindparams(id=uuid4(), url=DEFAULT_ROUTING_BASE_URL, created=now, updated=now)
    )


def downgrade() -> None:
    """Downgrade schema.

    Loses every fetched route. The map goes back to straight lines and
    the ladder back to whatever distances were typed, which is exactly
    where 0072 left it.
    """
    op.drop_table("road_trip_settings")
    op.drop_column("destinations", "route_error")
    op.drop_column("destinations", "route_fetched_at")
    op.drop_column("destinations", "route_geometry")
    op.drop_column("destinations", "route_miles")
