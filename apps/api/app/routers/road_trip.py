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
from app.reports.road_trip import haversine_miles
from app.schemas.destination import DestinationCreate, DestinationOut, DestinationUpdate
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


# --- destinations ------------------------------------------------------


def _with_straight_line(destination: Destination, home: Location | None) -> DestinationOut:
    out = DestinationOut.model_validate(destination)
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


@router.post("/destinations", response_model=DestinationOut, status_code=status.HTTP_201_CREATED)
async def create_destination(payload: DestinationCreate, db: AsyncSession = Depends(get_db)):
    destination = Destination(**payload.model_dump())
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
    return _with_straight_line(destination, await _home(db))


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
