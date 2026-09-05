import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# The OSRM public demo server. It answers without a key, which is the
# only reason it is the default — its own operators describe it as a demo
# and ask that it not be leaned on. PrintOps asks it a handful of
# questions when an admin edits a destination and never once while a
# dashboard renders, which is about as light as a caller can be; a
# district that would rather not depend on it at all points this at their
# own OSRM instead.
DEFAULT_ROUTING_BASE_URL = "https://router.project-osrm.org"


class RoadTripSettings(Base, TimestampMixin):
    """One row, same singleton pattern as ServerSettings and
    SnmpDefaultsSettings.

    Only the routing service lives here. The places themselves are rows
    in `locations` and `destinations`, because a district has many of
    those and one of this.
    """

    __tablename__ = "road_trip_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    routing_base_url: Mapped[str] = mapped_column(default=DEFAULT_ROUTING_BASE_URL)

    # Off means no destination ever asks for a route: distances stay
    # whatever an admin typed and the map draws every leg as a straight
    # dashed line. For an installation with no outbound internet that is
    # the honest configuration, and it is better than every save pausing
    # twenty seconds to fail.
    routing_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
