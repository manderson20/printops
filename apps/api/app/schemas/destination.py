from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DestinationOut(BaseModel):
    id: UUID
    name: str
    short_name: str | None
    miles: float
    latitude: float | None
    longitude: float | None
    enabled: bool
    # Straight-line miles from the home location, when both ends have
    # coordinates. Shown next to `miles` on the settings page as the
    # floor the real driving distance must sit above — it is never
    # written to `miles`, because the sentence claims a drive.
    straight_line_miles: float | None = None
    # What the road network measured, if it has been asked. `miles` is
    # normally a copy of this; the two differ when an admin has typed a
    # distance of their own, which is the one case where the ladder
    # should not follow the route.
    route_miles: float | None = None
    # Whether the map can draw this leg along real roads. The shape
    # itself is not sent here — it can be a few hundred points per leg,
    # and a settings page listing nine destinations has no use for it.
    # The dashboard gets it on the district report instead.
    has_route: bool = False
    route_fetched_at: datetime | None = None
    # Why the last attempt to fetch a route failed, or null. Carried on
    # the row so the admin who needs to see it is not required to be the
    # one who was there when it happened.
    route_error: str | None = None

    model_config = {"from_attributes": True}


class DestinationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    short_name: str | None = Field(default=None, max_length=120)
    # Optional, and normally left out: with coordinates and a routing
    # service, the road network measures the drive far better than
    # anybody types it. Give it to override that deliberately, or when
    # the rung has no coordinates to route between — "all the way to the
    # Moon" is a distance and nothing else.
    miles: float | None = Field(default=None, gt=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool = True

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self

    @model_validator(mode="after")
    def _has_something_to_measure(self):
        """Either somewhere to route to, or a distance already known. A
        rung with neither is not a rung."""
        if self.miles is None and self.latitude is None:
            raise ValueError("give either coordinates to route to, or a distance in miles")
        return self


class DestinationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    short_name: str | None = Field(default=None, max_length=120)
    miles: float | None = Field(default=None, gt=0)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    enabled: bool | None = None


class DestinationBulkCreate(BaseModel):
    destinations: list[DestinationCreate] = Field(min_length=1, max_length=25)


class DestinationSkipped(BaseModel):
    name: str
    reason: str


class DestinationBulkResult(BaseModel):
    """What became of each one, rather than a single pass/fail.

    Adding six suggested places at once means six routes fetched from a
    service that can time out on any of them. One failure must not
    discard the other five, and the admin has to be told which one it
    was.
    """

    added: list[DestinationOut]
    skipped: list["DestinationSkipped"]


class SuggestionOut(BaseModel):
    """A place the district could drive to, not yet a destination.

    Nothing is written when these are generated — the admin picks from
    them, and only what they pick is saved.
    """

    name: str
    short_name: str
    latitude: float
    longitude: float
    straight_line_miles: float
    population: int


class SuggestionsOut(BaseModel):
    # Echoed back so the caller can ask for this exact set again — a page
    # reload should not reshuffle a list somebody is reading.
    seed: int
    suggestions: list[SuggestionOut]


class RoadTripSettingsOut(BaseModel):
    routing_base_url: str
    routing_enabled: bool

    model_config = {"from_attributes": True}


class RoadTripSettingsUpdate(BaseModel):
    routing_base_url: str | None = Field(default=None, min_length=1, max_length=500)
    routing_enabled: bool | None = None
