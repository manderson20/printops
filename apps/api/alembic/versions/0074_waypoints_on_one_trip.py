"""make the destinations waypoints on one trip, in order

Revision ID: 0074
Revises: 0073
Create Date: 2026-09-05 14:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: Union[str, Sequence[str], None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    0073 gave every destination its own drive from home, and the map drew
    a fan of roads out of town. That was a defensible reading of a ladder
    whose rungs are distances from home, but it is not a road trip: a road
    trip visits places in an order, and the second leg starts where the
    first one finished.

    So destinations become waypoints. `position` is the order they are
    visited in, `leg_miles` is the drive from the one before, and
    `route_miles` becomes the sum of the legs up to here — how far the
    district must have driven to have reached this place *on this trip*.

    Every milestone therefore moves. Chicago was 397 miles as a direct
    drive from Brookfield; via Marceline and Jefferson City it is 537. The
    numbers do not change until somebody presses Refresh trip, because
    this migration makes no HTTP calls — but they will change, and the far
    ones change a lot. That is the honest distance for a trip that
    actually goes that way.

    `position` is backfilled in the order the rungs already sat in by
    distance, which is the itinerary an admin would most likely have
    chosen anyway: nearest first. Rungs with no coordinates get no
    position — "all the way to the Moon" is a distance, not a place to
    drive to, and it stays on the ladder by its own figure.
    """
    op.add_column("destinations", sa.Column("position", sa.Integer(), nullable=True))
    op.add_column("destinations", sa.Column("leg_miles", sa.Float(), nullable=True))

    # Existing per-destination geometry is a drive from home, which is not
    # what a leg is any more. Cleared rather than reinterpreted: a leg from
    # the previous waypoint is a different road, and leaving the old shape
    # in place would draw a fan that the distances no longer describe.
    op.execute(
        sa.text(
            "UPDATE destinations SET route_geometry = NULL, route_miles = NULL, route_error = NULL"
        )
    )

    # Nearest first, which is both the order they were listed in and the
    # order a trip would most naturally take.
    op.execute(
        sa.text(
            """
            UPDATE destinations AS d
            SET position = ordered.rank
            FROM (
                SELECT id, ROW_NUMBER() OVER (ORDER BY miles) AS rank
                FROM destinations
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            ) AS ordered
            WHERE d.id = ordered.id
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    Back to a fan of direct drives. The stored geometry is cleared again
    on the way down for the same reason it was cleared on the way up: a
    leg from the previous waypoint is not a drive from home, and drawing
    one as the other would be a picture that contradicts its own caption.
    """
    op.execute(
        sa.text(
            "UPDATE destinations SET route_geometry = NULL, route_miles = NULL, route_error = NULL"
        )
    )
    op.drop_column("destinations", "leg_miles")
    op.drop_column("destinations", "position")
