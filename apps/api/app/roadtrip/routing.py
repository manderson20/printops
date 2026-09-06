"""Asking a road network how far it actually is, and which way it goes.

The straight line between two pins is not the trip. Brookfield to
Jefferson City is 96 miles as the crow flies and 123 by road, and the
sentence on the dashboard claims a drive — so the number has to be the
drive, and the line on the map has to be the roads.

**Fetched once, at configuration time, and stored.** A route is asked for
when an admin adds or edits a destination, never while a dashboard is
being rendered. That matters for three reasons: a lobby screen must not
depend on an external service being up to draw a line it drew fine
yesterday; the roads between two towns do not change between page loads;
and a public routing service should not be asked the same question every
thirty seconds by a display nobody is standing at.

Speaks OSRM's HTTP API, which the public demo server implements and which
several self-hostable servers also speak. The base URL is a setting
rather than a constant precisely so a district that would rather not
depend on somebody else's demo server can point this at their own.
"""

from dataclasses import dataclass

import httpx

# A route is fetched while an admin waits on a form, so this can be
# generous compared with the 5 seconds app/integrations/classguard.py
# allows in the hot path of attributing a print job — but not so generous
# that a hung service makes the page look broken.
REQUEST_TIMEOUT_SECONDS = 20

METRES_PER_MILE = 1609.344

# OSRM's "simplified" overview still returns a few thousand points for a
# thousand-mile route. The map draws these at a zoom where the whole trip
# fits on one screen, so past a few hundred points per leg the extra
# fidelity is invisible and only costs payload and render time.
MAX_GEOMETRY_POINTS = 300


class RoutingError(Exception):
    """The road network could not be asked, or could not answer.

    Always recoverable: a destination without a route keeps its
    coordinates and its straight line, and the map falls back to drawing
    that leg dashed. Nothing about this failure should stop somebody
    adding a place.
    """


@dataclass(frozen=True)
class DrivingRoute:
    miles: float
    # [[latitude, longitude], ...] — in that order, because that is the
    # order Leaflet takes and the order every other coordinate pair in
    # this codebase is written in. OSRM answers in the opposite order and
    # the swap happens here, once, rather than at each of the places that
    # would otherwise have to remember.
    geometry: list[list[float]]


@dataclass(frozen=True)
class Itinerary:
    """One trip through every waypoint in order, leg by leg.

    Asked for in a single request rather than one per leg. That is not
    only politeness to the routing service: a trip routed all at once is
    guaranteed internally consistent, where three separate calls made
    minutes apart can disagree about a road that closed in between and
    leave the legs not meeting at the waypoint they share.
    """

    total_miles: float
    legs: list[DrivingRoute]


def _thin(points: list[list[float]], limit: int = MAX_GEOMETRY_POINTS) -> list[list[float]]:
    """Evenly drop points until at most `limit` remain, always keeping
    the two ends. Even sampling rather than Douglas-Peucker: the result is
    drawn as a whole-trip overview, and a simplification that preserved
    detail around corners would spend its budget on the ends of the route
    where the streets are."""
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    thinned = [points[round(index * step)] for index in range(limit)]
    thinned[-1] = points[-1]
    return thinned


async def fetch_driving_route(
    *,
    base_url: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> DrivingRoute:
    """The fastest driving route between two points.

    Coordinates are (latitude, longitude) in and out. OSRM's path takes
    them the other way round, which is a mistake worth making exactly
    once, here.
    """
    origin_latitude, origin_longitude = origin
    destination_latitude, destination_longitude = destination
    path = f"{origin_longitude},{origin_latitude};{destination_longitude},{destination_latitude}"
    url = f"{base_url.rstrip('/')}/route/v1/driving/{path}"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        try:
            response = await client.get(
                url, params={"overview": "simplified", "geometries": "geojson"}
            )
        except httpx.HTTPError as exc:
            raise RoutingError(f"Could not reach the routing service: {exc}") from exc

    if response.status_code != 200:
        raise RoutingError(
            f"The routing service returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RoutingError("The routing service returned something that wasn't JSON.") from exc

    # OSRM reports its own failures in the body with a 200, so this is a
    # real branch and not defensiveness: "NoRoute" is what comes back for
    # two points with no road between them, which is the answer for an
    # island, another continent, or the Moon.
    if payload.get("code") != "Ok":
        raise RoutingError(
            f"No driving route: {payload.get('code', 'unknown')}"
            f"{' — ' + payload['message'] if payload.get('message') else ''}"
        )
    routes = payload.get("routes") or []
    if not routes:
        raise RoutingError("The routing service found no route between those two places.")

    route = routes[0]
    coordinates = (route.get("geometry") or {}).get("coordinates") or []
    if not coordinates:
        raise RoutingError("The routing service returned a route with no shape.")

    return DrivingRoute(
        miles=round(route["distance"] / METRES_PER_MILE, 1),
        geometry=_thin([[point[1], point[0]] for point in coordinates]),
    )


async def fetch_itinerary(
    *,
    base_url: str,
    points: list[tuple[float, float]],
) -> Itinerary:
    """The whole trip: home, then every waypoint in order.

    `points` is home first and at least one waypoint after it. Comes back
    as one leg per hop, each with its own distance and shape, so a
    waypoint's milestone is the sum of the legs up to it — which is what
    "we have driven far enough to reach Jefferson City" means once the
    trip goes via Marceline first.

    Legs are thinned individually rather than the whole trip at once, so
    a short first leg keeps its shape instead of being sampled away by a
    long one later in the journey.
    """
    if len(points) < 2:
        raise RoutingError("A trip needs a start and at least one waypoint.")

    path = ";".join(f"{longitude},{latitude}" for latitude, longitude in points)
    url = f"{base_url.rstrip('/')}/route/v1/driving/{path}"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        try:
            response = await client.get(
                url,
                params={
                    "overview": "simplified",
                    "geometries": "geojson",
                    # One geometry per leg rather than one for the whole
                    # trip: a leg is what a waypoint owns, and splitting a
                    # combined line back into legs afterwards would mean
                    # guessing where one ended.
                    "steps": "true",
                },
            )
        except httpx.HTTPError as exc:
            raise RoutingError(f"Could not reach the routing service: {exc}") from exc

    if response.status_code != 200:
        raise RoutingError(
            f"The routing service returned HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RoutingError("The routing service returned something that wasn't JSON.") from exc

    if payload.get("code") != "Ok":
        raise RoutingError(
            f"No driving route: {payload.get('code', 'unknown')}"
            f"{' — ' + payload['message'] if payload.get('message') else ''}"
        )
    routes = payload.get("routes") or []
    if not routes:
        raise RoutingError("The routing service found no route through those places.")

    route = routes[0]
    legs = route.get("legs") or []
    if len(legs) != len(points) - 1:
        raise RoutingError(
            f"The routing service returned {len(legs)} legs for {len(points) - 1} hops."
        )

    built: list[DrivingRoute] = []
    for leg in legs:
        coordinates: list[list[float]] = []
        for step in leg.get("steps") or []:
            for point in (step.get("geometry") or {}).get("coordinates") or []:
                latitude, longitude = point[1], point[0]
                # Consecutive steps share their boundary coordinate, so
                # stitching them produces a duplicate at every join. They
                # are zero-length segments: they carry no shape, they cost
                # payload, and anything walking the line has to know to
                # step over them. Dropped here, once, rather than guarded
                # against at each place that reads a geometry.
                if coordinates and coordinates[-1] == [latitude, longitude]:
                    continue
                coordinates.append([latitude, longitude])
        if not coordinates:
            raise RoutingError("The routing service returned a leg with no shape.")
        built.append(
            DrivingRoute(
                miles=round(leg["distance"] / METRES_PER_MILE, 1), geometry=_thin(coordinates)
            )
        )

    return Itinerary(total_miles=round(route["distance"] / METRES_PER_MILE, 1), legs=built)
