from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DestinationOut(BaseModel):
    id: UUID
    name: str
    short_name: str | None
    # Where this waypoint falls in the trip, counting from 1. Null for a
    # rung that is a distance and not a place.
    position: int | None = None
    # How far the district must have driven to have reached here — every
    # leg of the trip up to and including this one.
    miles: float
    latitude: float | None
    longitude: float | None
    enabled: bool
    # Straight-line miles from the home location, when both ends have
    # coordinates. Shown next to `miles` on the settings page as the
    # floor the real driving distance must sit above — it is never
    # written to `miles`, because the sentence claims a drive.
    straight_line_miles: float | None = None
    # The drive from the previous waypoint, or from home for the first.
    leg_miles: float | None = None
    # The measured running total to here, owned by the recompute.
    route_miles: float | None = None
    # A distance an admin typed, or null when the ladder follows the road.
    # `miles` above is whichever of the two governs.
    miles_override: float | None = None
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


class DestinationOrder(BaseModel):
    """The order the trip is driven in.

    Every waypoint id, so a reorder is a statement about the whole trip
    rather than a nudge whose meaning depends on what else moved since the
    page was loaded.
    """

    destination_ids: list[UUID] = Field(min_length=1)


class ItineraryOut(BaseModel):
    """The trip as a whole. A waypoint knows its own leg and its own
    running total; only this knows where the journey ends."""

    waypoints: int
    total_miles: float | None
    last_driven_at: datetime | None
    # The reason the last attempt to drive the trip did not land, if it
    # did not. One reason for the whole trip, because the trip is routed
    # as one request.
    error: str | None


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
