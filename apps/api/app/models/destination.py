import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Destination(Base, TimestampMixin):
    """One rung of the road trip — a place far enough away to be worth
    reaching, measured in miles from the home Location.

    These were a tuple of frozen dataclasses in
    app/reports/equivalency_config.py, and for a single district that was
    the right call: nobody tunes how far Jefferson City is. What made it
    wrong was the word "Brookfield" appearing in a milestone name in
    source code. The rungs ship seeded with exactly the ladder that was
    compiled in, so an upgrade changes no sentence on any screen, and are
    editable from Settings from then on.

    A rung does not have to be a real place. "all the way to the Moon"
    has a distance and no coordinates, and that is the difference between
    a rung that can be drawn on the map and one that can only be reached
    — see `plottable`.
    """

    __tablename__ = "destinations"
    __table_args__ = (UniqueConstraint("name", name="uq_destinations_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # The whole achievement, as it reads when it is the thing announced:
    # "that's Brookfield to Jefferson City!".
    name: Mapped[str] = mapped_column(index=True)

    # The same rung named for the middle of a sentence, where the full
    # name would repeat something already said. "17% of the way to
    # Brookfield to Jefferson City" is not English; "17% of the way to
    # Jefferson City" is. Null when the two are the same.
    short_name: Mapped[str | None] = mapped_column(default=None)

    # The ladder's distance, and the only one the fun facts read: the
    # admin's override if they set one, otherwise what the road measured.
    # Stored rather than computed because the ladder sorts and filters on
    # it in SQL.
    miles: Mapped[float] = mapped_column(Float)

    latitude: Mapped[float | None] = mapped_column(Float, default=None)
    longitude: Mapped[float | None] = mapped_column(Float, default=None)

    # Where this waypoint falls in the trip, counting from 1. Null for a
    # rung that is a distance and not a place — "a lap of the football
    # field", "all the way to the Moon" — which sits on the ladder by its
    # own figure and is never driven to.
    #
    # Ordering is explicit rather than derived from distance, because in a
    # trip the order is the input and the distances are the result:
    # visiting Marceline before Jefferson City is a decision, and the 130
    # miles it takes to have done both follows from it.
    position: Mapped[int | None] = mapped_column(default=None)

    # --- the drive ------------------------------------------------
    #
    # Fetched from a routing service when the destination is saved, never
    # while a dashboard is rendered (app/roadtrip/routing.py explains
    # why). All three are null on a rung with no coordinates, and on one
    # whose route could not be fetched — in which case `route_error` says
    # what went wrong, so the settings page can show it rather than
    # leaving a silent blank.

    # The drive from the previous waypoint — or from home, for the first
    # one. What the routing service measured for this single hop.
    leg_miles: Mapped[float | None] = mapped_column(Float, default=None)

    # Every leg up to and including this one, added up: how far the
    # district has to have driven to have reached this place on this trip.
    # Owned entirely by the recompute — nothing else writes it.
    route_miles: Mapped[float | None] = mapped_column(Float, default=None)

    # A distance an admin typed, when they wanted one. Null means "use
    # what the road measured".
    #
    # Kept as its own column rather than inferred from `miles` differing
    # from `route_miles`, which is what the first version did and got
    # wrong: every recompute rewrote `miles` from the route, so an
    # override survived exactly until the next time anything changed. A
    # value nobody can distinguish from a stale copy is not a setting.
    #
    # `miles` above stays the one thing the ladder and the sentences read,
    # and is maintained as `miles_override or route_miles` wherever either
    # of those changes.
    miles_override: Mapped[float | None] = mapped_column(Float, default=None)

    # [[latitude, longitude], ...] along the roads for *this leg only* —
    # from the previous waypoint to this one. Drawn in order, so a trip
    # that doubles back retraces the same road and the numbered pins say
    # which way round it went.
    route_geometry: Mapped[list | None] = mapped_column(JSON, default=None)

    route_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Why the last attempt failed, or null. Kept on the row rather than
    # only reported in the response that failed, because the admin who
    # needs to see it is often not the one who was there when it happened.
    route_error: Mapped[str | None] = mapped_column(default=None)

    # Off keeps a rung out of the ladder without deleting it — the way to
    # drop "the Moon" for a term and get it back, without losing the
    # figure someone looked up.
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")

    @property
    def is_overridden(self) -> bool:
        """Whether the ladder is following a typed figure rather than the
        road. Drives the one line on the settings page that admits the two
        can disagree."""
        return self.miles_override is not None

    def settle_miles(self) -> None:
        """Point `miles` at whichever figure currently governs.

        Called after anything writes an override or a measured distance,
        so there is one place that knows the precedence rather than four
        that each remember it.
        """
        if self.miles_override is not None:
            self.miles = self.miles_override
        elif self.route_miles is not None:
            self.miles = self.route_miles

    @property
    def has_route(self) -> bool:
        """Whether the map can draw this leg along real roads rather than
        as a straight dashed line."""
        return bool(self.route_geometry)

    @property
    def plottable(self) -> bool:
        """Whether this rung can be a pin on the map, and therefore a
        waypoint on the trip. A destination without coordinates still
        counts on the ladder and still gets its sentence — it just isn't
        a place with a location on Earth, or nobody has looked its
        coordinates up yet."""
        return self.latitude is not None and self.longitude is not None

    @property
    def label(self) -> str:
        return self.short_name or self.name
