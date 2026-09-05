"""give the district its own places and its own road trip

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-05 04:50:00.000000

"""

from datetime import UTC, datetime
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: Union[str, Sequence[str], None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The distance ladder exactly as it was compiled into
# app/reports/equivalency_config.py, converted from feet to miles, so that
# upgrading changes no sentence on any screen. Coordinates are town
# centres, good to a few hundred feet, which is far finer than a milestone
# that claims "120 miles" needs.
FEET_PER_MILE = 5280.0

_SEED_DESTINATIONS = [
    # (name, short_name, miles, latitude, longitude)
    ("a lap of the football field", None, 1_040.0 / FEET_PER_MILE, None, None),
    ("a quarter-mile lap of the track", "a quarter mile", 0.25, None, None),
    ("Brookfield to Marceline", "Marceline", 12.0, 39.7117, -92.9477),
    ("Brookfield to Jefferson City", "Jefferson City", 120.0, 38.5767, -92.1735),
    ("all the way across Missouri", "across Missouri", 300.0, None, None),
    ("Brookfield to Chicago", "Chicago", 410.0, 41.8781, -87.6298),
    ("coast to coast", None, 2_800.0, None, None),
    ("all the way around the Earth", "around the Earth", 24_901.0, None, None),
    ("all the way to the Moon", "the Moon", 238_900.0, None, None),
]


def upgrade() -> None:
    """Upgrade schema.

    Two tables that between them take the district out of the source code.

    Until now the road trip started in Brookfield, Missouri because
    "Brookfield" was written into a tuple of frozen dataclasses. That is
    correct for exactly one district and unusable for the next one, and it
    also meant the buildings PrintOps already knows about — every distinct
    value anyone has typed into a printer's `building` field — had nowhere
    to keep an address.

    `locations` is those places, with an address and, where someone has
    looked them up, coordinates. One of them is home: the point the road
    trip measures from.

    `destinations` is the ladder itself. It is seeded with the rungs that
    were compiled in, converted to miles, so no fact anybody has already
    read changes underneath them on upgrade. Rungs that name a real place
    get coordinates and can be drawn; "all the way to the Moon" keeps its
    distance and is simply never a pin.

    The seeded home location carries Brookfield's coordinates and no
    street address — which is precisely what the constant it replaces
    asserted, no more. An installation that is not Brookfield renames one
    row instead of editing Python.
    """
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("street", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("postal_code", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("is_home", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_locations_name"),
    )
    op.create_index(op.f("ix_locations_name"), "locations", ["name"], unique=False)

    op.create_table(
        "destinations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("miles", sa.Float(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_destinations_name"),
    )
    op.create_index(op.f("ix_destinations_name"), "destinations", ["name"], unique=False)

    # A real timestamp rather than sa.func.now(): bulk_insert sends multiple
    # rows through executemany, which binds each value as a parameter and
    # cannot render a SQL function per row.
    now = datetime.now(UTC)
    op.bulk_insert(
        sa.table(
            "locations",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.String()),
            sa.column("city", sa.String()),
            sa.column("state", sa.String()),
            sa.column("latitude", sa.Float()),
            sa.column("longitude", sa.Float()),
            sa.column("is_home", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": uuid4(),
                "name": "Brookfield R-III",
                "city": "Brookfield",
                "state": "MO",
                "latitude": 39.7864,
                "longitude": -93.0735,
                "is_home": True,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    op.bulk_insert(
        sa.table(
            "destinations",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.String()),
            sa.column("short_name", sa.String()),
            sa.column("miles", sa.Float()),
            sa.column("latitude", sa.Float()),
            sa.column("longitude", sa.Float()),
            sa.column("enabled", sa.Boolean()),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        ),
        [
            {
                "id": uuid4(),
                "name": name,
                "short_name": short_name,
                "miles": miles,
                "latitude": latitude,
                "longitude": longitude,
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            for name, short_name, miles, latitude, longitude in _SEED_DESTINATIONS
        ],
    )


def downgrade() -> None:
    """Downgrade schema.

    Dropping these puts the ladder back in the hands of the constants in
    equivalency_config.py, which is what the code falls back to when no
    destination is enabled — so a downgrade loses whatever an admin
    edited, and the district's milestones go back to Brookfield's.
    """
    op.drop_index(op.f("ix_destinations_name"), table_name="destinations")
    op.drop_table("destinations")
    op.drop_index(op.f("ix_locations_name"), table_name="locations")
    op.drop_table("locations")
