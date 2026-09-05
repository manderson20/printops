import uuid

from sqlalchemy import Float, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Location(Base, TimestampMixin):
    """One of the district's own places — a building, the bus barn, the
    admin office — with an address good enough to put a pin on a map.

    Printers already carry a free-text `building` (app/models/printer.py),
    and this table does not replace it. It gives those names somewhere to
    hang an address, and it gives the district one thing it never had: a
    home address that isn't compiled in. Until this existed the origin of
    every "Brookfield to Jefferson City" milestone was a constant in
    app/reports/equivalency_config.py, which is fine for one district and
    useless for the next one to install PrintOps.

    Deliberately *not* a foreign key on Printer yet. Matching is by name
    against Printer.building, which is exactly as reliable as the
    free-text field it matches — the settings page shows which building
    names have no Location and which Locations match no printer, so the
    mismatches are visible rather than silent. Making it a real relation
    means a data migration over names people have been typing by hand for
    a year, and that is its own change.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("name", name="uq_locations_name"),
        # At most one home, held by the database rather than by the code
        # that sets it. Two "Make home" requests racing can each see a
        # world with only the original home in it, each clear that one and
        # each set their own; this is what stops the second one landing.
        # Partial, so every other row is free to be false.
        # Partial on both dialects. A dialect-specific `where` that the
        # running database does not recognise is silently dropped, leaving
        # a *full* unique index on a boolean — which would allow one home
        # and exactly one other location. SQLite is what the tests build
        # their schema on, so it needs saying there too.
        Index(
            "uq_locations_single_home",
            "is_home",
            unique=True,
            postgresql_where=text("is_home"),
            sqlite_where=text("is_home"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Matched against Printer.building, so it reads best as the building
    # name people already type there rather than a formal legal name.
    name: Mapped[str] = mapped_column(index=True)

    street: Mapped[str | None] = mapped_column(default=None)
    city: Mapped[str | None] = mapped_column(default=None)
    state: Mapped[str | None] = mapped_column(default=None)
    postal_code: Mapped[str | None] = mapped_column(default=None)

    # Where the pin actually goes. Nullable because an address someone
    # typed is useful on its own — it just cannot be drawn — and because
    # PrintOps does no geocoding: there is no address lookup service
    # configured here, and adding an outbound dependency on one to draw a
    # map is a bigger decision than this table. An admin pastes the two
    # numbers from any map site; the settings page says so.
    latitude: Mapped[float | None] = mapped_column(Float, default=None)
    longitude: Mapped[float | None] = mapped_column(Float, default=None)

    # The one the road trip starts from. At most one row has this, and
    # that is enforced twice on purpose: the router clears the flag from
    # the others so "Make home" works rather than fails, and the partial
    # unique index above is the guard for the case the clear did not see.
    is_home: Mapped[bool] = mapped_column(default=False, server_default="false")

    notes: Mapped[str | None] = mapped_column(default=None)

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
