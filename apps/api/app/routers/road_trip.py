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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.roadtrip.itinerary import ItineraryResult
from app.roadtrip.itinerary import recompute as recompute_itinerary
from app.roadtrip.places import MAX_SEARCH_RESULTS
from app.roadtrip.places import search as search_places
from app.roadtrip.places import suggest as suggest_places
from app.schemas.destination import (
    DestinationBulkCreate,
    DestinationBulkResult,
    DestinationCreate,
    DestinationOrder,
    DestinationOut,
    DestinationSkipped,
    DestinationUpdate,
    ItineraryOut,
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
            # Every leg starts from home, so a new home makes every stored
            # distance and every drawn road describe a journey from
            # somewhere the trip no longer begins.
            await recompute_itinerary(db)
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

    home_moved = bool(fields.get("is_home")) or (
        location.is_home and any(key in fields for key in ("latitude", "longitude"))
    )
    if fields.get("is_home"):
        await _clear_other_homes(db, location.id)
    if home_moved:
        await db.flush()
        await recompute_itinerary(db)
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
    was_home = location.is_home
    await db.delete(location)
    await db.flush()
    if was_home:
        # No home means no trip to draw. The recompute records that on
        # every waypoint rather than leaving legs that start nowhere.
        await recompute_itinerary(db)
    await db.commit()


# --- the trip ----------------------------------------------------------


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


def _needs_a_distance(destination: Destination) -> bool:
    return not destination.miles or destination.miles <= 0


def _out(destination: Destination, home: Location | None) -> DestinationOut:
    """One destination as the settings page sees it.

    The straight-line figure is attached here rather than stored: it is
    two coordinates and a formula, it changes whenever home moves, and a
    stored copy would be one more thing that can be stale.
    """
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


async def _ordered(db: AsyncSession) -> list[Destination]:
    """Waypoints in the order they are driven, then the rungs that are a
    distance and not a place, by their own figure. That is the order the
    settings page lists them in and the order a reader would expect: the
    trip, and then the things beyond the end of it."""
    rows = list((await db.execute(select(Destination))).scalars())
    return sorted(rows, key=lambda d: (d.position is None, d.position or 0, d.miles))


async def _recompute_and_commit(db: AsyncSession) -> ItineraryResult:
    """Re-drive the trip, then commit whatever it decided.

    Every mutation that can change the shape of the itinerary ends here,
    because a waypoint's milestone is the sum of the legs before it — add
    a place in the middle and everything after it moves.
    """
    result = await recompute_itinerary(db)
    await db.commit()
    return result


@router.get("/destinations", response_model=list[DestinationOut])
async def list_destinations(db: AsyncSession = Depends(get_db)):
    home = await _home(db)
    return [_out(destination, home) for destination in await _ordered(db)]


@router.get("/itinerary", response_model=ItineraryOut)
async def get_itinerary(db: AsyncSession = Depends(get_db)):
    """The trip as a whole: how many stops, how far, and when it was last
    driven. What the settings page puts at the top of the page, and the
    only place the total appears — a waypoint knows its own leg and its
    own running total, not the end of the journey."""
    waypoints = [d for d in await _ordered(db) if d.plottable]
    driven = [d for d in waypoints if d.route_miles is not None]
    return ItineraryOut(
        waypoints=len(waypoints),
        total_miles=driven[-1].route_miles if driven else None,
        last_driven_at=max(
            (d.route_fetched_at for d in driven if d.route_fetched_at), default=None
        ),
        error=next((d.route_error for d in waypoints if d.route_error), None),
    )


@router.post("/itinerary/route", response_model=ItineraryOut)
async def refresh_itinerary(db: AsyncSession = Depends(get_db)):
    """Drive the whole trip again.

    One request to the routing service for the entire itinerary, not one
    per leg: a trip routed all at once is internally consistent, where
    separate calls made minutes apart can disagree about a road that
    closed in between and leave two legs not meeting at the waypoint they
    share.

    This replaces whatever distances the waypoints had, which is the point
    of pressing it. An admin who wants their own figure for one of them
    edits it afterwards.
    """
    await _recompute_and_commit(db)
    return await get_itinerary(db)


@router.put("/destinations/order", response_model=list[DestinationOut])
async def reorder_destinations(payload: DestinationOrder, db: AsyncSession = Depends(get_db)):
    """Set the order the trip is driven in.

    Takes every waypoint id, so a reorder is a statement about the whole
    trip rather than a nudge whose meaning depends on what else moved
    since the page was loaded. Anything missing from the list is refused
    rather than quietly left at the end.

    Reordering changes every distance after the first thing that moved, so
    the trip is re-driven before this returns.
    """
    waypoints = [d for d in await _ordered(db) if d.plottable]
    by_id = {d.id: d for d in waypoints}
    if set(payload.destination_ids) != set(by_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The order must list every waypoint exactly once.",
        )

    for index, destination_id in enumerate(payload.destination_ids, start=1):
        by_id[destination_id].position = index

    await _recompute_and_commit(db)
    home = await _home(db)
    return [_out(destination, home) for destination in await _ordered(db)]


@router.post("/destinations", response_model=DestinationOut, status_code=status.HTTP_201_CREATED)
async def create_destination(payload: DestinationCreate, db: AsyncSession = Depends(get_db)):
    """Add one place.

    A waypoint joins the end of the trip; where it belongs is a decision
    for the reorder control, not something to infer from how far away it
    happens to be. Its distance is whatever the trip works out to once it
    has been driven.
    """
    fields = payload.model_dump()
    typed_miles = fields.pop("miles", None)
    destination = Destination(**fields, miles_override=typed_miles)
    destination.miles = typed_miles
    if destination.plottable and destination.miles is None:
        # A placeholder so the row is valid before the trip is driven. It
        # is replaced by the real cumulative figure moments later, and if
        # the routing fails the create is refused rather than leaving this
        # standing as if it meant something.
        destination.miles = 0.0
    db.add(destination)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A destination named {payload.name!r} already exists.",
        ) from exc

    if destination.plottable:
        result = await recompute_itinerary(db)
        if _needs_a_distance(destination):
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Couldn't measure the trip"
                    f"{' — ' + result.error if result.error else ''}. "
                    "Enter a distance in miles yourself, or try again."
                ),
            )

    await db.commit()
    await db.refresh(destination)
    return _out(destination, await _home(db))


@router.post(
    "/destinations/bulk",
    response_model=DestinationBulkResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_destinations(payload: DestinationBulkCreate, db: AsyncSession = Depends(get_db)):
    """Add several at once — what the suggestion tool sends.

    Every one is added, and then the trip is driven **once**. That is the
    whole reason this endpoint exists rather than the client posting six
    times: six separate creates would re-drive the trip six times, and the
    first five answers would all be thrown away by the sixth.
    """
    existing = {name for (name,) in (await db.execute(select(Destination.name))).all()}
    added: list[Destination] = []
    skipped: list[DestinationSkipped] = []

    for item in payload.destinations:
        if item.name in existing:
            skipped.append(DestinationSkipped(name=item.name, reason="Already on the list."))
            continue
        item_fields = item.model_dump()
        typed = item_fields.pop("miles", None)
        destination = Destination(**item_fields, miles_override=typed)
        destination.miles = typed
        if destination.plottable and destination.miles is None:
            destination.miles = 0.0
        db.add(destination)
        existing.add(item.name)
        added.append(destination)

    if not added:
        await db.rollback()
        return DestinationBulkResult(added=[], skipped=skipped)

    await db.flush()
    result = await recompute_itinerary(db)

    # A place no road reaches fails the whole trip, because OSRM is asked
    # for one route through all of them — and the failure names the trip,
    # not the stop. So the good ones are found by elimination: take them
    # all back out, then put them back one at a time and keep whichever
    # ones the road can still be driven with.
    #
    # That is one routing request per new waypoint, which is exactly what
    # this endpoint exists to avoid — but only on the failure path, and
    # only for the handful somebody just added. Losing five valid places
    # because the sixth was on an island is the worse trade.
    if not result.ok:
        candidates = [d for d in added if d.plottable]
        for destination in candidates:
            await db.delete(destination)
        await db.flush()

        kept: list[Destination] = []
        for item in payload.destinations:
            candidate = next((d for d in candidates if d.name == item.name), None)
            if candidate is None:
                continue
            item_fields = item.model_dump()
            typed = item_fields.pop("miles", None)
            retry = Destination(**item_fields, miles_override=typed)
            retry.miles = typed if typed is not None else 0.0
            db.add(retry)
            await db.flush()
            attempt = await recompute_itinerary(db)
            if attempt.ok:
                kept.append(retry)
                continue
            await db.delete(retry)
            await db.flush()
            skipped.append(DestinationSkipped(name=item.name, reason=attempt.error or "No route."))

        added = [d for d in added if not d.plottable] + kept
        await recompute_itinerary(db)

    await db.commit()
    home = await _home(db)
    fresh = {d.id: d for d in await _ordered(db)}
    return DestinationBulkResult(
        added=[_out(fresh[d.id], home) for d in added if d.id in fresh],
        skipped=skipped,
    )


@router.post("/destinations/suggest", response_model=SuggestionsOut)
async def suggest_destinations(seed: int | None = None, db: AsyncSession = Depends(get_db)):
    """Places the district could drive to, expanding outward.

    Writes nothing. The admin picks from what comes back and sends the
    picks to /destinations/bulk, which is where the trip actually gets
    driven — so generating a list a dozen times costs the routing service
    nothing at all.

    Distances here are straight lines from home, not trip distances. They
    are a rough sense of how far out each suggestion is, which is what the
    choice needs; what it becomes on the ladder depends on where it lands
    in the order, and that is not known until it is added.

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


@router.get("/places/search", response_model=list[SuggestionOut])
async def search_for_a_place(
    q: str = Query(min_length=2, max_length=80),
    limit: int = Query(MAX_SEARCH_RESULTS, ge=1, le=MAX_SEARCH_RESULTS),
    db: AsyncSession = Depends(get_db),
):
    """Find a town by name, so a place can be added without looking its
    coordinates up anywhere.

    Reads the bundled gazetteer, which goes down to a thousand people —
    the small town fifteen minutes down the road is exactly what somebody
    searches for, and it is the one thing the suggestion tool cannot
    offer. Nothing is written; the result is fed to the ordinary create.

    Works with no home location, which is the point: a district setting
    itself up needs to find its own town before it has one. Distances
    come back as zero in that case rather than as a guess, and the caller
    shows no distance rather than a wrong one.
    """
    home = await _home(db)
    latitude = home.latitude if home and home.has_coordinates else None
    longitude = home.longitude if home and home.has_coordinates else None

    return [
        SuggestionOut(
            name=(f"{home.name} to {result.place.label}" if home else result.place.label),
            short_name=result.place.label,
            latitude=result.place.latitude,
            longitude=result.place.longitude,
            straight_line_miles=result.straight_line_miles,
            population=result.place.population,
        )
        for result in search_places(
            q, home_latitude=latitude, home_longitude=longitude, limit=limit
        )
    ]


@router.patch("/destinations/{destination_id}", response_model=DestinationOut)
async def update_destination(
    destination_id: UUID, payload: DestinationUpdate, db: AsyncSession = Depends(get_db)
):
    destination = await db.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found.")

    fields = payload.model_dump(exclude_unset=True)
    moved = any(key in fields for key in ("latitude", "longitude"))

    # `miles` on this model is the ladder's answer, not a field to assign
    # to. The edit form resubmits whatever is currently displayed, so a
    # coordinate change would otherwise arrive carrying a measured figure
    # and be mistaken for somebody insisting on it. Only a value that
    # differs from what the road last said is an intention; null clears
    # the override and hands the ladder back to the measurement.
    if "miles" in fields:
        typed = fields.pop("miles")
        if typed is None or (
            destination.route_miles is not None and abs(typed - destination.route_miles) <= 0.05
        ):
            destination.miles_override = None
        else:
            destination.miles_override = typed

    for key, value in fields.items():
        setattr(destination, key, value)
    destination.settle_miles()

    if (destination.latitude is None) != (destination.longitude is None):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="latitude and longitude must be given together.",
        )

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another destination already has that name.",
        ) from exc

    # Moving a waypoint changes its leg and every leg after it. Renaming
    # one does not, and re-driving the whole trip to change a label would
    # be a routing request bought for nothing.
    # Pausing or resuming changes which places are on the road, so the
    # trip has to be re-driven for the same reason moving one does.
    if moved or "enabled" in fields:
        await recompute_itinerary(db)
    destination.settle_miles()

    await db.commit()
    await db.refresh(destination)
    return _out(destination, await _home(db))


@router.delete("/destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_destination(destination_id: UUID, db: AsyncSession = Depends(get_db)):
    """Removing a waypoint shortens the trip, so everything after it moves
    closer. The last destination can go like any other: an empty ladder is
    not an error state, it falls back to the one compiled into
    app/reports/equivalency_config.py."""
    destination = await db.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found.")
    was_waypoint = destination.plottable
    await db.delete(destination)
    await db.flush()
    if was_waypoint:
        await recompute_itinerary(db)
    await db.commit()


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
