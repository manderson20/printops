import uuid

from sqlalchemy import Float, UniqueConstraint
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
    __table_args__ = (UniqueConstraint("name", name="uq_locations_name"),)

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

    # The one the road trip starts from. At most one row has this — the
    # router clears it from the others when it is set, rather than a
    # partial unique index, so the rule holds identically on every
    # database this runs on and lives where the reader can see it.
    is_home: Mapped[bool] = mapped_column(default=False, server_default="false")

    notes: Mapped[str | None] = mapped_column(default=None)

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
