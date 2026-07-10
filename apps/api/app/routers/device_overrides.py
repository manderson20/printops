from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.integrations.mosyle import normalize_mac
from app.models.device_override import DeviceUserOverride
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceUser
from app.models.job import Job
from app.models.mosyle import MosyleDevice
from app.schemas.device_override import (
    DeviceOverrideOut,
    DeviceOverrideUpdate,
    KnownDeviceOut,
    KnownDevicePage,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


def _device_matches(device: KnownDeviceOut, search: str) -> bool:
    haystack = " ".join(
        filter(
            None,
            [
                device.mac_address,
                device.device_name,
                device.serial_number,
                device.reported_email,
                device.reported_username,
                device.override_email,
            ],
        )
    ).lower()
    return search.lower() in haystack


@router.get("", response_model=KnownDevicePage)
async def list_known_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Union of both MDM device caches, each merged with whatever admin
    override currently applies — the view used to spot and fix an
    ambiguous/wrong attribution (e.g. a bare local username shared by
    multiple people; see app/attribution/resolve.py). A Google Workspace
    ChromeOS fleet can easily be thousands of devices, so this is
    paginated the same way as app/routers/users.py's list_users — built
    and sorted in Python first (small per-device merge, not worth a more
    complex query), then sliced for the page."""
    overrides = {
        o.mac_address: o for o in (await db.execute(select(DeviceUserOverride))).scalars().all()
    }

    devices: list[KnownDeviceOut] = []

    mosyle_devices = (await db.execute(select(MosyleDevice))).scalars().all()
    for d in mosyle_devices:
        override = overrides.get(d.mac_address)
        devices.append(
            KnownDeviceOut(
                mac_address=d.mac_address,
                source="mosyle",
                serial_number=d.serial_number,
                device_name=d.device_name,
                reported_email=d.user_email,
                reported_username=d.user_name,
                override_email=override.resolved_email if override else None,
                override_note=override.note if override else None,
            )
        )

    google_devices = (await db.execute(select(GoogleWorkspaceDevice))).scalars().all()
    for d in google_devices:
        override = overrides.get(d.mac_address)
        devices.append(
            KnownDeviceOut(
                mac_address=d.mac_address,
                source="google_workspace",
                serial_number=d.serial_number,
                device_name=d.device_name,
                reported_email=d.user_email,
                override_email=override.resolved_email if override else None,
                override_note=override.note if override else None,
            )
        )

    devices.sort(key=lambda d: d.mac_address)

    if search:
        devices = [d for d in devices if _device_matches(d, search)]

    total = len(devices)
    start = (page - 1) * page_size
    page_devices = devices[start : start + page_size]

    return KnownDevicePage(items=page_devices, total=total, page=page, page_size=page_size)


@router.put("/{mac_address}/override", response_model=DeviceOverrideOut)
async def set_device_override(
    mac_address: str, payload: DeviceOverrideUpdate, db: AsyncSession = Depends(get_db)
):
    """Sets (or corrects) the canonical email for a specific device, and
    immediately backfills that same device's already-logged jobs —
    scoped strictly to Job rows whose mac_address matches this exact
    device, never a blind rename of every job sharing the old ambiguous
    string. Older jobs logged before Job.mac_address existed can't be
    backfilled (the MAC was never captured for them)."""
    mac = normalize_mac(mac_address)
    email = payload.resolved_email.strip().lower()

    roster_match = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email)
    )
    if roster_match.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.resolved_email}' is not in the synced Google Workspace user "
                "roster — sync Google Workspace settings, or double-check the address."
            ),
        )

    result = await db.execute(
        select(DeviceUserOverride).where(DeviceUserOverride.mac_address == mac)
    )
    override = result.scalar_one_or_none()
    if override is None:
        override = DeviceUserOverride(mac_address=mac, resolved_email=email, note=payload.note)
        db.add(override)
    else:
        override.resolved_email = email
        override.note = payload.note

    backfill_result = await db.execute(
        Job.__table__.update()
        .where(Job.mac_address == mac)
        .values(submitted_by=email, attribution_method="override")
    )

    await db.commit()
    await db.refresh(override)
    return DeviceOverrideOut(
        mac_address=override.mac_address,
        resolved_email=override.resolved_email,
        note=override.note,
        created_at=override.created_at,
        updated_at=override.updated_at,
        backfilled_job_count=backfill_result.rowcount or 0,
    )


@router.delete("/{mac_address}/override", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device_override(mac_address: str, db: AsyncSession = Depends(get_db)):
    mac = normalize_mac(mac_address)
    result = await db.execute(
        select(DeviceUserOverride).where(DeviceUserOverride.mac_address == mac)
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No override set for this device."
        )
    await db.delete(override)
    await db.commit()
