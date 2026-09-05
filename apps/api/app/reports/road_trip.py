"""The road trip: the district's own distance ladder, and the route drawn
from it.

app/reports/equivalency.py already knows how to place a total on a ladder
and say which rung it has passed. What it could not do was let the ladder
belong to the district — the rungs were frozen dataclasses naming
Brookfield, Missouri, which is right for one installation and wrong for
every other one. This module turns the `destinations` table into that
same Ladder, and turns it a second time into something a map can draw.

Two things stay deliberately apart here:

  * **Miles are road miles.** They are what the sentence claims ("that's
    Brookfield to Jefferson City"), and a reader checks them against
    their own drive. Nothing recomputes them from coordinates.
  * **Coordinates are for drawing.** The line between two pins is
    straight, because PrintOps has no routing service and inventing one
    to bend a line would be a network dependency bought for decoration.

So the marker's position along a leg is interpolated by *mileage* and
drawn on a *straight* line. It is honest about how far the district has
travelled and approximate about where that lands on the ground, which is
the right way round for a picture whose job is "look how far we've come".
"""

import math
from dataclasses import dataclass

from app.models.destination import Destination
from app.models.location import Location
from app.reports.equivalency_config import DISTANCE_LADDER, FEET_PER_MILE, Ladder, Milestone

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in miles.

    Offered to an admin as a starting point when they add a destination —
    the straight-line figure is always shorter than the drive, so it is a
    floor to correct upward, never an answer to accept unread. Not used
    to compute anything the district sees.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


def ladder_from_destinations(destinations: list[Destination]) -> Ladder:
    """The distance ladder this district actually uses.

    Falls back to the built-in ladder when nothing is configured or
    everything is switched off. That fallback is not a formality: it is
    what keeps the fun facts working on an installation whose admin has
    never opened the Road Trip page, and what a downgrade lands on.

    Rungs come back in feet because that is the unit the distance
    equivalency is computed in (app/reports/equivalency.py:distance_feet),
    and converting the total instead would mean rounding the thing being
    measured rather than the thing it is measured against.
    """
    rungs = tuple(
        Milestone(
            name=destination.name,
            value=destination.miles * FEET_PER_MILE,
            short=destination.short_name,
        )
        for destination in destinations
        if destination.enabled and destination.miles > 0
    )
    if not rungs:
        return DISTANCE_LADDER
    return Ladder(key="distance", unit="feet", rungs=rungs)


@dataclass(frozen=True)
class RouteStop:
    """A pin on the map: a destination that has coordinates."""

    name: str
    label: str
    miles: float
    latitude: float
    longitude: float
    reached: bool


@dataclass(frozen=True)
class RoutePosition:
    """Where the district has got to, as a point to put the marker on."""

    latitude: float
    longitude: float
    # The leg it is on, for the caption: "between Marceline and Jefferson
    # City". Either end can be absent — before the first stop the journey
    # is still leaving home, and past the last plottable one there is
    # nothing ahead to name.
    from_label: str | None
    to_label: str | None


@dataclass(frozen=True)
class Route:
    home_name: str
    home_latitude: float
    home_longitude: float
    miles_travelled: float
    stops: tuple[RouteStop, ...]
    position: RoutePosition | None


def _interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, fraction: float
) -> tuple[float, float]:
    """A point `fraction` of the way along the straight line between two
    pins. Linear in degrees, which over the distances a school district
    travels on paper is indistinguishable from anything more careful, and
    which cannot produce a point off the line the map is drawing."""
    fraction = min(max(fraction, 0.0), 1.0)
    return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)


def build_route(
    home: Location | None,
    destinations: list[Destination],
    distance_feet: float,
) -> Route | None:
    """The map's whole payload, or None when there is nothing to draw.

    Nothing to draw means: no home location, or a home location nobody
    has put coordinates on, or no enabled destination that has
    coordinates. Each of those is a state an admin can see and fix on the
    settings page, and each of them yields a dashboard with no map rather
    than a dashboard with a broken one.

    Stops that carry no coordinates are left out of the route while
    remaining on the ladder — "coast to coast" still gets its sentence,
    it just isn't a pin. That is why the marker's leg is described by the
    stops on either side of it *on the map*, which may skip rungs the
    ladder counts.
    """
    if home is None or home.latitude is None or home.longitude is None:
        return None

    plottable = sorted(
        (
            destination
            for destination in destinations
            if destination.enabled and destination.plottable and destination.miles > 0
        ),
        key=lambda destination: destination.miles,
    )
    if not plottable:
        return None

    miles_travelled = max(distance_feet, 0.0) / FEET_PER_MILE
    stops = tuple(
        RouteStop(
            name=destination.name,
            label=destination.label,
            miles=destination.miles,
            latitude=destination.latitude,
            longitude=destination.longitude,
            reached=miles_travelled >= destination.miles,
        )
        for destination in plottable
    )

    position = _position_on(
        home_latitude=home.latitude,
        home_longitude=home.longitude,
        stops=stops,
        miles_travelled=miles_travelled,
    )

    return Route(
        home_name=home.name,
        home_latitude=home.latitude,
        home_longitude=home.longitude,
        miles_travelled=miles_travelled,
        stops=stops,
        position=position,
    )


def _position_on(
    *,
    home_latitude: float,
    home_longitude: float,
    stops: tuple[RouteStop, ...],
    miles_travelled: float,
) -> RoutePosition | None:
    """The marker. None only when the journey hasn't started.

    Zero miles is a real answer and not a marker at home: a district that
    has printed nothing this period is not "at home about to leave", it
    has no journey yet, and a pin sitting on the school would read as
    progress.
    """
    if miles_travelled <= 0:
        return None

    previous_miles = 0.0
    previous_latitude, previous_longitude = home_latitude, home_longitude
    previous_label: str | None = None

    for stop in stops:
        if miles_travelled < stop.miles:
            leg = stop.miles - previous_miles
            fraction = (miles_travelled - previous_miles) / leg if leg > 0 else 1.0
            latitude, longitude = _interpolate(
                previous_latitude, previous_longitude, stop.latitude, stop.longitude, fraction
            )
            return RoutePosition(latitude, longitude, previous_label, stop.label)
        previous_miles = stop.miles
        previous_latitude, previous_longitude = stop.latitude, stop.longitude
        previous_label = stop.label

    # Past every pin on the map. The marker sits on the last one rather
    # than being extrapolated into the ocean beyond it.
    last = stops[-1]
    return RoutePosition(last.latitude, last.longitude, last.label, None)
