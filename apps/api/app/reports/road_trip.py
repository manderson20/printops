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
  * **Coordinates are for drawing.** Each destination also carries the
    shape of the drive to it, fetched once when it was configured
    (app/roadtrip/routing.py). The map follows those roads.

Every route runs from home, not from the previous stop, because that is
what the ladder means: a rung is "far enough to have reached Chicago",
measured from home, not "far enough to have reached Chicago having gone
via Des Moines". Chaining the legs would quietly turn 410 miles into
1,200. So the picture is every road out of town, with the one currently
being driven picked out.

A destination whose route was never fetched — no coordinates, no routing
service, a request that timed out — falls back to a straight line between
the two pins. That is a visibly poorer drawing of a real fact, which is
the right failure: the milestone is still true, it is just drawn as the
crow flies until somebody presses Refresh route.
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
    """A pin on the map, and the road that gets to it."""

    name: str
    label: str
    miles: float
    latitude: float
    longitude: float
    reached: bool
    # [[latitude, longitude], ...] from home along real roads, or None
    # when no route has been fetched — in which case the caller draws the
    # straight line between home and the pin instead.
    geometry: list[list[float]] | None
    # The one the district is currently driving towards: the nearest stop
    # it has not reached. Exactly one stop has this, unless every stop is
    # behind us or the journey has not started.
    is_target: bool = False


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

    # The nearest stop not yet reached — the one being driven towards, and
    # the only leg the marker can be on. None once every pin is behind us.
    target = next((d for d in plottable if miles_travelled < d.miles), None)

    stops = tuple(
        RouteStop(
            name=destination.name,
            label=destination.label,
            miles=destination.miles,
            latitude=destination.latitude,
            longitude=destination.longitude,
            reached=miles_travelled >= destination.miles,
            geometry=destination.route_geometry or None,
            is_target=destination is target,
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


def _along(geometry: list[list[float]], fraction: float) -> tuple[float, float]:
    """The point `fraction` of the way along a polyline, measured by the
    polyline's own length rather than by its point count.

    Measuring by point count would be wrong in exactly the way that
    matters here: a simplified route carries far more points through a
    city than across two hundred miles of interstate, so "halfway through
    the points" can be twenty miles from home on a journey of four
    hundred.

    Segment lengths are computed in degrees, not miles. Over one route
    the distortion is a constant factor on every segment and cancels in
    the ratio, and using real distances here would mean a haversine per
    segment per page load to move a dot a few pixels.
    """
    fraction = min(max(fraction, 0.0), 1.0)
    if len(geometry) < 2:
        point = geometry[0]
        return point[0], point[1]

    lengths = [
        math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(geometry, geometry[1:], strict=False)
    ]
    total = sum(lengths)
    if total <= 0:
        point = geometry[0]
        return point[0], point[1]

    remaining = total * fraction
    for (start, end), length in zip(
        zip(geometry, geometry[1:], strict=False), lengths, strict=False
    ):
        if remaining <= length or length == 0:
            share = (remaining / length) if length else 0.0
            return (
                start[0] + (end[0] - start[0]) * share,
                start[1] + (end[1] - start[1]) * share,
            )
        remaining -= length

    last = geometry[-1]
    return last[0], last[1]


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

    The marker rides the road to the target stop when that road is known,
    and the straight line to it when it is not. Either way its distance
    along that leg is the district's real mileage as a fraction of the
    leg's own — the position is approximate, the progress is not.
    """
    if miles_travelled <= 0:
        return None

    previous_label: str | None = None
    for stop in stops:
        if stop.is_target:
            fraction = miles_travelled / stop.miles if stop.miles > 0 else 1.0
            if stop.geometry:
                latitude, longitude = _along(stop.geometry, fraction)
            else:
                latitude, longitude = _interpolate(
                    home_latitude, home_longitude, stop.latitude, stop.longitude, fraction
                )
            return RoutePosition(latitude, longitude, previous_label, stop.label)
        previous_label = stop.label

    # Past every pin on the map. The marker sits on the last one rather
    # than being extrapolated into the ocean beyond it.
    last = stops[-1]
    return RoutePosition(last.latitude, last.longitude, last.label, None)
