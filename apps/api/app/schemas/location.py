from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class LocationOut(BaseModel):
    id: UUID
    name: str
    street: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    latitude: float | None
    longitude: float | None
    is_home: bool
    notes: str | None

    model_config = {"from_attributes": True}


class LocationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    # Bounds are the real ones, not sanity guesses: a latitude outside
    # ±90 is not a typo to be tolerated, it is a value no map can plot.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_home: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self):
        """Half a coordinate is not a location. Rejecting it here means
        every consumer can treat "has a latitude" as "can be drawn"
        without also checking the longitude."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class LocationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    is_home: bool | None = None
    notes: str | None = None


class UnmatchedBuildingOut(BaseModel):
    """A building name printers are using that has no Location row.

    Surfaced so the gap between the free-text field people have been
    typing for a year and this table is visible on the page rather than
    silently absent — see app/models/location.py on why the two are
    matched by name instead of related by a foreign key.
    """

    building: str
    printer_count: int
