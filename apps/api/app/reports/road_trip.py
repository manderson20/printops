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

The trip is one journey through the waypoints in order, and a rung is
"far enough to have reached Chicago **on this trip**" — via Marceline and
Jefferson City, if that is where the trip goes first. So a waypoint's
distance is the sum of every leg before it, not the direct drive from
home: Chicago is 537 miles into this trip and 397 as a drive of its own,
and the ladder uses the first because that is the road actually drawn
beneath it.

A trip can double back — Marceline is north-west of Brookfield and
Jefferson City is south-east, so the second leg retraces the first before
carrying on. The legs are drawn in order, later over earlier, and the
waypoints are numbered; a retraced stretch is one road on the map and the
numbers say which way round it went.

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
    # Where it falls in the trip, counting from 1 — the number on the pin.
    position: int
    # Every leg up to and including this one: how far the district has to
    # have driven to have got here.
    miles: float
    # Just this leg, from the previous waypoint.
    leg_miles: float
    latitude: float
    longitude: float
    reached: bool
    # [[latitude, longitude], ...] along real roads for *this leg* — from
    # the previous waypoint to this one — or None when no route has been
    # fetched, in which case the caller draws the straight line between
    # the two pins instead.
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

    Rungs that carry no coordinates are left out of the trip while
    remaining on the ladder — "coast to coast" still gets its sentence, it
    just isn't a place the trip drives to. That is why the marker's leg is
    described by the waypoints on either side of it *on the map*, which
    may skip rungs the ladder counts.

    Waypoints come back in the order they are driven, not by distance.
    Those are usually the same order and deliberately need not be: an
    admin who wants the trip to go somewhere further before somewhere
    nearer is describing a real journey, and the cumulative distances
    follow from their choice rather than overruling it.
    """
    if home is None or home.latitude is None or home.longitude is None:
        return None

    waypoints = sorted(
        (
            destination
            for destination in destinations
            if destination.enabled and destination.plottable and destination.miles > 0
        ),
        key=lambda d: (d.position is None, d.position or 0, d.miles),
    )
    if not waypoints:
        return None

    miles_travelled = max(distance_feet, 0.0) / FEET_PER_MILE

    # The nearest waypoint not yet reached — the one being driven towards,
    # and the only leg the marker can be on. None once the whole trip is
    # behind us.
    target = next((d for d in waypoints if miles_travelled < d.miles), None)

    stops: list[RouteStop] = []
    previous_miles = 0.0
    for index, destination in enumerate(waypoints, start=1):
        # Falls back to the difference between running totals when the
        # trip has never been driven, so a ladder of typed distances still
        # produces sensible legs.
        leg = destination.leg_miles
        if leg is None:
            leg = max(destination.miles - previous_miles, 0.0)
        stops.append(
            RouteStop(
                name=destination.name,
                label=destination.label,
                position=index,
                miles=destination.miles,
                leg_miles=leg,
                latitude=destination.latitude,
                longitude=destination.longitude,
                reached=miles_travelled >= destination.miles,
                geometry=destination.route_geometry or None,
                is_target=destination is target,
            )
        )
        previous_miles = destination.miles

    position = _position_on(
        home_latitude=home.latitude,
        home_longitude=home.longitude,
        stops=tuple(stops),
        miles_travelled=miles_travelled,
    )

    return Route(
        home_name=home.name,
        home_latitude=home.latitude,
        home_longitude=home.longitude,
        miles_travelled=miles_travelled,
        stops=tuple(stops),
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

    The marker rides the leg it is on — the road from the last waypoint
    reached to the one being driven towards — placed by how far into that
    leg the district has got. Its distance along the leg is real; where
    that lands on the ground is approximate, because the leg is drawn from
    a simplified shape. That is the right way round for a picture.
    """
    if miles_travelled <= 0:
        return None

    previous_label: str | None = None
    previous_miles = 0.0
    for stop in stops:
        if stop.is_target:
            into_leg = miles_travelled - previous_miles
            # Measured against the interval the *ladder* shows, not the
            # road's own length. Those differ when a distance was typed
            # over the measured one, and the marker has to agree with the
            # numbers beside it: a bar that says 60% of the way to
            # Jefferson City with the pin already sitting on Jefferson
            # City is the picture contradicting its own caption.
            shown_leg = stop.miles - previous_miles
            fraction = into_leg / shown_leg if shown_leg > 0 else 1.0
            if stop.geometry:
                latitude, longitude = _along(stop.geometry, fraction)
            else:
                # No road for this leg, so the straight line between the
                # two pins — or between home and the first pin.
                start_latitude = home_latitude
                start_longitude = home_longitude
                for earlier in stops:
                    if earlier is stop:
                        break
                    start_latitude, start_longitude = earlier.latitude, earlier.longitude
                latitude, longitude = _interpolate(
                    start_latitude, start_longitude, stop.latitude, stop.longitude, fraction
                )
            return RoutePosition(latitude, longitude, previous_label, stop.label)
        previous_label = stop.label
        previous_miles = stop.miles

    # Past every waypoint. The marker sits on the last one rather than
    # being extrapolated into the ocean beyond it.
    last = stops[-1]
    return RoutePosition(last.latitude, last.longitude, last.label, None)
