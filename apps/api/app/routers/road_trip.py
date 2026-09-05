"""Settings for the district's own places and its own road trip.

Two resources that only make sense together: `locations` are the
district's buildings, one of which is home, and `destinations` are the
rungs of the milestone ladder measured from that home. Both were
constants in app/reports/equivalency_config.py until now — see
app/models/location.py and app/models/destination.py for why they moved.

Admin-only throughout. What the dashboard needs from these two tables
rides on the district report itself
(app/routers/reports.py:report_district_fun_facts), so nothing here has
to be readable by an ordinary signed-in user: this router edits the
configuration, it is not how the map gets its data.
"""

import random
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.destination import Destination
from app.models.location import Location
from app.models.printer import Printer
from app.models.road_trip_settings import RoadTripSettings
from app.reports.road_trip import haversine_miles
from app.roadtrip.places import suggest as suggest_places
from app.roadtrip.routing import RoutingError, fetch_driving_route
from app.schemas.destination import (
    DestinationBulkCreate,
    DestinationBulkResult,
    DestinationCreate,
    DestinationOut,
    DestinationSkipped,
    DestinationUpdate,
    RoadTripSettingsOut,
    RoadTripSettingsUpdate,
    SuggestionOut,
    SuggestionsOut,
)
from app.schemas.location import (
    LocationCreate,
    LocationOut,
    LocationUpdate,
    UnmatchedBuildingOut,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


async def _home(db: AsyncSession) -> Location | None:
    return (
        await db.execute(select(Location).where(Location.is_home.is_(True)))
    ).scalar_one_or_none()


async def _clear_other_homes(db: AsyncSession, keep: UUID) -> None:
    """At most one home, enforced here rather than by a partial unique
    index so the rule reads the same on every database this runs on and
    sits where somebody changing it will see it."""
    others = (
        await db.execute(select(Location).where(Location.is_home.is_(True), Location.id != keep))
    ).scalars()
    for location in others:
        location.is_home = False


# --- locations ---------------------------------------------------------


@router.get("/locations", response_model=list[LocationOut])
async def list_locations(db: AsyncSession = Depends(get_db)):
    """Home first, then alphabetical — the home location is the one that
    changes what every other page says, so it does not belong buried at
    L for Lincoln."""
    result = await db.execute(select(Location).order_by(Location.is_home.desc(), Location.name))
    return list(result.scalars())


@router.get("/locations/unmatched-buildings", response_model=list[UnmatchedBuildingOut])
async def list_unmatched_buildings(db: AsyncSession = Depends(get_db)):
    """Building names on printers that no Location claims.

    The point of the page is to close this gap, so the gap is what it
    leads with. Compared case-insensitively and with surrounding
    whitespace ignored, because these are two free-text fields typed by
    different people at different times and "High School " matching
    "High School" is not a coincidence worth ignoring.
    """
    known = {
        (name or "").strip().casefold()
        for name in (await db.execute(select(Location.name))).scalars()
    }
    rows = (
        await db.execute(
            select(Printer.building, func.count(Printer.id))
            .where(Printer.building.is_not(None), Printer.building != "")
            .group_by(Printer.building)
            .order_by(func.count(Printer.id).desc(), Printer.building)
        )
    ).all()
    return [
        UnmatchedBuildingOut(building=building, printer_count=count)
        for building, count in rows
        if building.strip().casefold() not in known
    ]


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
async def create_location(payload: LocationCreate, db: AsyncSession = Depends(get_db)):
    location = Location(**payload.model_dump())
    db.add(location)
    try:
        # Flushed inside the guard, not before it: the unique name is
        # enforced by the database, and on SQLite it is the flush that
        # raises, not the commit.
        await db.flush()
        if location.is_home:
            await _clear_other_homes(db, location.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A location named {payload.name!r} already exists.",
        ) from exc
    await db.refresh(location)
    return location


@router.patch("/locations/{location_id}", response_model=LocationOut)
async def update_location(
    location_id: UUID, payload: LocationUpdate, db: AsyncSession = Depends(get_db)
):
    location = await db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found.")

    fields = payload.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(location, key, value)

    # Checked after applying, so that clearing one half of a pair in the
    # same request as setting the other is judged on the result rather
    # than on the order the fields arrived in.
    if (location.latitude is None) != (location.longitude is None):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="latitude and longitude must be given together.",
        )

    if fields.get("is_home"):
        await _clear_other_homes(db, location.id)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another location already has that name.",
        ) from exc
    await db.refresh(location)
    return location


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(location_id: UUID, db: AsyncSession = Depends(get_db)):
    """Deleting home is allowed and costs the map, not the facts.

    Refusing would mean an admin cannot remove a building that closed
    without first nominating another one, and the consequence is neither
    silent nor destructive: with no home there is no route to draw, the
    dashboard shows the milestones without a map, and the settings page
    says so.
    """
    location = await db.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found.")
    await db.delete(location)
    await db.commit()


# --- the routing service -----------------------------------------------


async def _settings(db: AsyncSession) -> RoadTripSettings:
    """The singleton, created on first read if 0073 never seeded it —
    same lazy pattern the other settings singletons use, so a database
    restored from a dump taken before that migration still works."""
    row = (await db.execute(select(RoadTripSettings))).scalars().first()
    if row is None:
        row = RoadTripSettings()
        db.add(row)
        await db.flush()
    return row


def _home_point(home: Location | None) -> tuple[float, float] | None:
    """Home as two plain numbers.

    Deliberately not the ORM object. A rollback anywhere in a bulk add
    expires every loaded instance, and the next read of `home.latitude`
    would then be lazy IO from inside async code — which SQLAlchemy stops
    with a MissingGreenlet rather than a wrong answer, but stops either
    way. Two floats cannot expire.
    """
    if home is None or not home.has_coordinates:
        return None
    return (home.latitude, home.longitude)


async def _apply_route(
    db: AsyncSession, destination: Destination, home: tuple[float, float] | None
) -> None:
    """Ask the road network how far it is and which way it goes, and
    record the answer on the destination.

    Called when a destination is saved, never while a dashboard renders.
    Every failure is recorded and swallowed: a destination the routing
    service could not reach is still a destination, and the map falls
    back to a dashed straight line for that leg. The one thing that is
    not swallowed is the case where the caller gave no distance either —
    the router checks for that after this returns, because a rung with no
    distance is not a rung.

    An explicitly given `miles` is left alone. That is the difference
    between the ladder's distance and the measured one: an admin who
    types 120 because that is what the road signs say keeps 120 through
    every refetch.
    """
    if not destination.plottable or home is None:
        return
    settings = await _settings(db)
    if not settings.routing_enabled:
        destination.route_error = "Routing is switched off in Settings."
        return
    base_url = settings.routing_base_url

    try:
        route = await fetch_driving_route(
            base_url=base_url,
            origin=home,
            destination=(destination.latitude, destination.longitude),
        )
    except RoutingError as exc:
        destination.route_error = str(exc)[:500]
        return

    destination.route_miles = route.miles
    destination.route_geometry = route.geometry
    destination.route_fetched_at = datetime.now(UTC)
    destination.route_error = None


def _needs_a_distance(destination: Destination) -> bool:
    return not destination.miles or destination.miles <= 0


# --- destinations ------------------------------------------------------


def _with_straight_line(destination: Destination, home: Location | None) -> DestinationOut:
    out = DestinationOut.model_validate(destination).model_copy(
        update={"has_route": destination.has_route}
    )
    if home is not None and home.has_coordinates and destination.plottable:
        out = out.model_copy(
            update={
                "straight_line_miles": round(
                    haversine_miles(
                        home.latitude, home.longitude, destination.latitude, destination.longitude
                    ),
                    1,
                )
            }
        )
    return out


@router.get("/destinations", response_model=list[DestinationOut])
async def list_destinations(db: AsyncSession = Depends(get_db)):
    """In the order they are travelled, which is the order the ladder
    uses and the only order a road trip has."""
    home = await _home(db)
    result = await db.execute(select(Destination).order_by(Destination.miles))
    return [_with_straight_line(destination, home) for destination in result.scalars()]


async def _build_destination(
    db: AsyncSession, payload: DestinationCreate, home: tuple[float, float] | None
) -> Destination:
    """A destination with its drive already measured, not yet committed.

    Distance comes from the road network unless the caller insisted on
    one. Shared by the single and bulk creates so the two cannot drift on
    what "how far is it" means.
    """
    destination = Destination(**payload.model_dump())
    await _apply_route(db, destination, home)
    if payload.miles is None and destination.route_miles is not None:
        destination.miles = destination.route_miles
    return destination


@router.post("/destinations", response_model=DestinationOut, status_code=status.HTTP_201_CREATED)
async def create_destination(payload: DestinationCreate, db: AsyncSession = Depends(get_db)):
    home = await _home(db)
    destination = await _build_destination(db, payload, _home_point(home))
    if _needs_a_distance(destination):
        # Coordinates but no route and no typed distance: there is nothing
        # to put on the ladder, and falling back to the straight line
        # would put a crow-flies number inside a sentence claiming a
        # drive. Say so instead.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Couldn't measure the drive"
                f"{' — ' + destination.route_error if destination.route_error else ''}. "
                "Enter the distance in miles yourself, or try again."
            ),
        )
    db.add(destination)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A destination named {payload.name!r} already exists.",
        ) from exc
    await db.refresh(destination)
    return _with_straight_line(destination, home)


@router.post(
    "/destinations/bulk",
    response_model=DestinationBulkResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_destinations(payload: DestinationBulkCreate, db: AsyncSession = Depends(get_db)):
    """Add several at once — what the suggestion tool sends.

    Each one is committed on its own. Six routes fetched from a service
    that can time out on any of them must not mean six lost when one
    fails, and the admin has to be told which one it was rather than
    seeing five appear and being left to work out the sixth.
    """
    home = await _home(db)
    home_point = _home_point(home)
    added: list[DestinationOut] = []
    skipped: list[DestinationSkipped] = []

    for item in payload.destinations:
        destination = await _build_destination(db, item, home_point)
        if _needs_a_distance(destination):
            # Nothing was added for this one, so there is nothing to roll
            # back, and a rollback here would only expire what the
            # earlier iterations left loaded.
            skipped.append(
                DestinationSkipped(
                    name=item.name,
                    reason=destination.route_error or "Couldn't measure the drive.",
                )
            )
            continue
        db.add(destination)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            skipped.append(DestinationSkipped(name=item.name, reason="Already on the list."))
            continue
        await db.refresh(destination)
        added.append(_with_straight_line(destination, home))

    return DestinationBulkResult(added=added, skipped=skipped)


@router.post("/destinations/{destination_id}/route", response_model=DestinationOut)
async def refresh_destination_route(destination_id: UUID, db: AsyncSession = Depends(get_db)):
    """Ask the road network again.

    The button for a destination added before the home location had
    coordinates, or while the routing service was unreachable, or whose
    distance shipped as an estimate — the nine seeded rungs all start
    that way, because 0073 deliberately makes no HTTP calls from inside a
    migration.

    A refetch replaces `miles` with what it measures. That is the point of
    pressing it; an admin who wants their own figure edits it afterwards,
    and that edit survives every later save because only this endpoint
    overwrites it.
    """
    destination = await db.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found.")
    if not destination.plottable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This destination has no coordinates, so there is nothing to route to.",
        )
    home = await _home(db)
    if home is None or not home.has_coordinates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No home location with coordinates to measure from.",
        )

    await _apply_route(db, destination, _home_point(home))
    if destination.route_miles is not None:
        destination.miles = destination.route_miles
    await db.commit()
    await db.refresh(destination)
    return _with_straight_line(destination, home)


@router.post("/destinations/suggest", response_model=SuggestionsOut)
async def suggest_destinations(seed: int | None = None, db: AsyncSession = Depends(get_db)):
    """Places the district could drive to, expanding outward.

    Writes nothing. The admin picks from what comes back and sends the
    picks to /destinations/bulk, which is where routes actually get
    fetched — so generating a list a dozen times costs the routing
    service nothing at all.

    `seed` reproduces a set. Without one a fresh seed is drawn and echoed
    back, which is what lets a page reload show the same list rather than
    reshuffling under somebody reading it.
    """
    home = await _home(db)
    if home is None or not home.has_coordinates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Suggestions are measured from the home location — set one with "
                "coordinates in Settings > Locations first."
            ),
        )

    if seed is None:
        seed = random.randrange(1_000_000)

    rows = (await db.execute(select(Destination.name, Destination.short_name))).all()
    taken = frozenset(label for row in rows for label in (row.name, row.short_name) if label)

    return SuggestionsOut(
        seed=seed,
        suggestions=[
            SuggestionOut(
                # The full name is the achievement as it is announced —
                # "that's Brookfield R-III to Des Moines, IA!" — and the
                # short one is the same rung mid-sentence.
                name=f"{home.name} to {suggestion.place.label}",
                short_name=suggestion.place.label,
                latitude=suggestion.place.latitude,
                longitude=suggestion.place.longitude,
                straight_line_miles=suggestion.straight_line_miles,
                population=suggestion.place.population,
            )
            for suggestion in suggest_places(
                home_latitude=home.latitude,
                home_longitude=home.longitude,
                taken_labels=taken,
                seed=seed,
            )
        ],
    )


# --- the routing service, as a setting ---------------------------------


@router.get("/settings", response_model=RoadTripSettingsOut)
async def get_road_trip_settings(db: AsyncSession = Depends(get_db)):
    settings = await _settings(db)
    await db.commit()
    return settings


@router.put("/settings", response_model=RoadTripSettingsOut)
async def update_road_trip_settings(
    payload: RoadTripSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _settings(db)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, key, value)
    await db.commit()
    await db.refresh(settings)
    return settings


@router.patch("/destinations/{destination_id}", response_model=DestinationOut)
async def update_destination(
    destination_id: UUID, payload: DestinationUpdate, db: AsyncSession = Depends(get_db)
):
    destination = await db.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found.")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(destination, key, value)

    if (destination.latitude is None) != (destination.longitude is None):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="latitude and longitude must be given together.",
        )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another destination already has that name.",
        ) from exc
    await db.refresh(destination)
    return _with_straight_line(destination, await _home(db))


@router.delete("/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_destination(destination_id: UUID, db: AsyncSession = Depends(get_db)):
    """The last destination can be deleted like any other. An empty
    ladder is not an error state: the district falls back to the one
    compiled into app/reports/equivalency_config.py, which is what a
    fresh install runs on until somebody opens this page."""
    destination = await db.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found.")
    await db.delete(destination)
    await db.commit()
