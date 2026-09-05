"""Recomputing the trip.

A waypoint's milestone is not a property of that waypoint. It is the sum
of every leg before it, so adding a place in the middle, removing one, or
dragging one up the list changes the distance of everything after it. The
only coherent unit of work is therefore *the whole trip*, and that is what
this module does: one routing request, every waypoint rewritten, or
nothing rewritten at all.

Nothing here runs while a dashboard is rendered. It is called when an
admin changes the itinerary, and by the Refresh trip button.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.destination import Destination
from app.models.location import Location
from app.models.road_trip_settings import RoadTripSettings
from app.roadtrip.routing import RoutingError, fetch_itinerary

logger = logging.getLogger(__name__)

# OSRM's public demo caps a request's coordinate count, and a trip with
# more stops than this is not a trip anybody reads off a lobby screen
# anyway. Waypoints beyond it keep whatever distance they have and are
# left out of the drive.
MAX_WAYPOINTS = 24


@dataclass(frozen=True)
class ItineraryResult:
    """What a recompute did, in terms the settings page can show."""

    waypoints: int
    total_miles: float | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


async def waypoints_in_order(db: AsyncSession) -> list[Destination]:
    """The trip, in the order it is driven.

    Only destinations with coordinates: a rung that is a distance and not
    a place is on the ladder but not on the road. Rows with no position
    yet sort last by their own distance, so a waypoint added before its
    first recompute joins the end rather than silently jumping the queue.

    Paused waypoints are left out, because the report leaves them out too
    (app/reports/road_trip.py filters on `enabled`). Routing through a
    stop the map never draws would give every waypoint after it a leg
    starting from a place nobody can see — disconnected roads, and a
    marker positioned along a journey that is not the one on screen.
    """
    rows = (
        await db.execute(
            select(Destination).where(
                Destination.latitude.is_not(None),
                Destination.longitude.is_not(None),
                Destination.enabled.is_(True),
            )
        )
    ).scalars()
    return sorted(
        rows,
        key=lambda d: (d.position is None, d.position or 0, d.miles),
    )


async def renumber(db: AsyncSession) -> list[Destination]:
    """Close gaps and settle ties so `position` is 1..n with no holes.

    Called before every recompute rather than only after a reorder,
    because a delete leaves a hole and an add leaves a null, and a trip
    whose order depends on tie-breaking is a trip that can reorder itself
    when nothing changed.
    """
    ordered = await waypoints_in_order(db)
    for index, waypoint in enumerate(ordered, start=1):
        waypoint.position = index
    return ordered


async def recompute(db: AsyncSession) -> ItineraryResult:
    """Re-drive the whole trip and write every leg back.

    All or nothing. A partial itinerary — some legs from this attempt and
    some from an earlier one — would have cumulative distances that do not
    add up to the road drawn beneath them, which is worse than an
    itinerary that is visibly out of date and says so.

    A failure is recorded on every waypoint and swallowed. The trip keeps
    the distances it had, the map keeps the shapes it had, and the
    settings page shows why the last attempt did not land.
    """
    ordered = await renumber(db)
    if not ordered:
        return ItineraryResult(waypoints=0, total_miles=None, error=None)

    home = (
        await db.execute(select(Location).where(Location.is_home.is_(True)))
    ).scalar_one_or_none()
    if home is None or not home.has_coordinates:
        return _fail(ordered, "No home location with coordinates to start the trip from.")

    settings = (await db.execute(select(RoadTripSettings))).scalars().first()
    if settings is None:
        settings = RoadTripSettings()
        db.add(settings)
        await db.flush()
    if not settings.routing_enabled:
        return _fail(ordered, "Routing is switched off in Settings.")

    driven = ordered[:MAX_WAYPOINTS]
    points = [(home.latitude, home.longitude)] + [(w.latitude, w.longitude) for w in driven]

    try:
        trip = await fetch_itinerary(base_url=settings.routing_base_url, points=points)
    except RoutingError as exc:
        logger.warning("road trip: could not route the itinerary: %s", exc)
        return _fail(ordered, str(exc)[:500])

    now = datetime.now(UTC)
    cumulative = 0.0
    for waypoint, leg in zip(driven, trip.legs, strict=True):
        cumulative += leg.miles
        waypoint.leg_miles = leg.miles
        waypoint.route_miles = round(cumulative, 1)
        waypoint.route_geometry = leg.geometry
        waypoint.route_fetched_at = now
        waypoint.route_error = None
        # Never assigns over an override. `settle_miles` knows the
        # precedence; this loop only reports what the road said.
        waypoint.settle_miles()

    await _clear_routes_of_paused_waypoints(db)

    for waypoint in ordered[MAX_WAYPOINTS:]:
        waypoint.leg_miles = None
        waypoint.route_miles = None
        waypoint.route_geometry = None
        waypoint.route_error = (
            f"Only the first {MAX_WAYPOINTS} waypoints are driven — this one is beyond that."
        )

    return ItineraryResult(waypoints=len(driven), total_miles=trip.total_miles, error=None)


def _fail(waypoints: list[Destination], reason: str) -> ItineraryResult:
    """Record why, on every waypoint, and leave their figures alone.

    The distances stay because they are the last thing that was true. A
    trip that blanked itself every time a routing service hiccuped would
    lose a correct answer to a temporary problem.
    """
    for waypoint in waypoints:
        waypoint.route_error = reason
    return ItineraryResult(waypoints=len(waypoints), total_miles=None, error=reason)


async def _clear_routes_of_paused_waypoints(db: AsyncSession) -> None:
    """A paused waypoint keeps its coordinates and its typed distance and
    loses its leg.

    It is not on the trip any more, so the road it used to be reached by
    starts from a place that is no longer before it. Keeping that shape
    would draw a leg the itinerary does not contain the moment somebody
    un-pauses something else.
    """
    paused = (
        await db.execute(
            select(Destination).where(
                Destination.enabled.is_(False), Destination.route_geometry.is_not(None)
            )
        )
    ).scalars()
    for waypoint in paused:
        waypoint.leg_miles = None
        waypoint.route_miles = None
        waypoint.route_geometry = None
        waypoint.route_fetched_at = None
