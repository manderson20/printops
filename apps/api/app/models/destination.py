import uuid

from sqlalchemy import Float, UniqueConstraint
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

    # Road miles from home, not great-circle. The settings page computes
    # the straight-line distance from the coordinates as a starting
    # point, but what goes in here is what an admin decides the journey
    # is, because the sentence claims a drive and a reader will check it
    # against their own.
    miles: Mapped[float] = mapped_column(Float)

    latitude: Mapped[float | None] = mapped_column(Float, default=None)
    longitude: Mapped[float | None] = mapped_column(Float, default=None)

    # Off keeps a rung out of the ladder without deleting it — the way to
    # drop "the Moon" for a term and get it back, without losing the
    # figure someone looked up.
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")

    @property
    def plottable(self) -> bool:
        """Whether this rung can be a pin on the map. A destination
        without coordinates still counts on the ladder and still gets its
        sentence — it just isn't a place with a location on Earth, or
        nobody has looked its coordinates up yet."""
        return self.latitude is not None and self.longitude is not None

    @property
    def label(self) -> str:
        return self.short_name or self.name
