from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.integrations.google_workspace import org_unit_matches
from app.models.google_workspace import GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.staff_copier_identity import StaffCopierIdentity
from app.schemas.staff_copier_identity import (
    MissingStaffIdentityOut,
    StaffCopierIdentityCreate,
    StaffCopierIdentityOut,
    StaffCopierIdentityUpdate,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


async def _get_identity_or_404(identity_id: UUID, db: AsyncSession) -> StaffCopierIdentity:
    identity = await db.get(StaffCopierIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Copier identity not found")
    return identity


async def _validate_roster_email(email: str, db: AsyncSession) -> str:
    """Same validate-against-roster contract as
    app/routers/device_overrides.py:set_device_override — a copier
    identity can only be assigned to a real, currently-synced staff
    member, never an arbitrary string that could drift from the roster."""
    normalized = email.strip().lower()
    result = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == normalized)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{email}' is not in the synced Google Workspace user roster — sync Google "
                "Workspace settings, or double-check the address."
            ),
        )
    return normalized


async def _check_duplicate(
    db: AsyncSession,
    identity_type: str,
    identity_value: str,
    mfp_device_id: UUID | None,
    exclude_id: UUID | None = None,
) -> None:
    """App-layer uniqueness check on (identity_type, identity_value,
    mfp_device_id) — not a DB constraint, since NULL doesn't reliably
    constrain duplicates the same way across SQLite/Postgres. See
    StaffCopierIdentity's docstring."""
    stmt = select(StaffCopierIdentity).where(
        StaffCopierIdentity.identity_type == identity_type,
        StaffCopierIdentity.identity_value == identity_value,
        StaffCopierIdentity.mfp_device_id == mfp_device_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(StaffCopierIdentity.id != exclude_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"That {identity_type} is already assigned to {existing.staff_email}"
                + (" for this device" if mfp_device_id else " org-wide")
                + "."
            ),
        )


@router.get("", response_model=list[StaffCopierIdentityOut])
async def list_staff_copier_identities(
    staff_email: str | None = None,
    identity_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(StaffCopierIdentity)
    if staff_email is not None:
        stmt = stmt.where(func.lower(StaffCopierIdentity.staff_email) == staff_email.strip().lower())
    if identity_type is not None:
        stmt = stmt.where(StaffCopierIdentity.identity_type == identity_type)
    result = await db.execute(stmt.order_by(StaffCopierIdentity.staff_email))
    return result.scalars().all()


@router.get("/missing", response_model=list[MissingStaffIdentityOut])
async def list_staff_missing_copier_identity(db: AsyncSession = Depends(get_db)):
    """Roster members with zero copier identities recorded — mirrors the
    copier-PIN-roster OU filter (app/routers/settings.py:
    export_copier_pin_roster) so this doesn't warn about students in
    districts where the same roster covers both."""
    settings_result = await db.execute(select(GoogleWorkspaceSettings).limit(1))
    settings = settings_result.scalar_one_or_none()

    users_result = await db.execute(select(GoogleWorkspaceUser).order_by(GoogleWorkspaceUser.email))
    users = users_result.scalars().all()
    if settings and settings.staff_org_unit_path:
        users = [u for u in users if org_unit_matches(u.org_unit_path, settings.staff_org_unit_path)]

    emails_with_identity = {
        row[0].strip().lower()
        for row in (await db.execute(select(StaffCopierIdentity.staff_email))).all()
    }

    return [
        MissingStaffIdentityOut(email=u.email, name=u.name, employee_id=u.employee_id)
        for u in users
        if u.email.strip().lower() not in emails_with_identity
    ]


@router.get("/by-staff/{email}", response_model=list[StaffCopierIdentityOut])
async def get_staff_copier_identities_by_email(email: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StaffCopierIdentity)
        .where(func.lower(StaffCopierIdentity.staff_email) == email.strip().lower())
        .order_by(StaffCopierIdentity.identity_type)
    )
    return result.scalars().all()


@router.post("", response_model=StaffCopierIdentityOut, status_code=status.HTTP_201_CREATED)
async def create_staff_copier_identity(
    payload: StaffCopierIdentityCreate, db: AsyncSession = Depends(get_db)
):
    email = await _validate_roster_email(payload.staff_email, db)
    await _check_duplicate(db, payload.identity_type, payload.identity_value, payload.mfp_device_id)

    identity = StaffCopierIdentity(
        staff_email=email,
        identity_type=payload.identity_type,
        identity_value=payload.identity_value,
        mfp_device_id=payload.mfp_device_id,
        note=payload.note,
    )
    db.add(identity)
    await db.commit()
    await db.refresh(identity)
    return identity


@router.patch("/{identity_id}", response_model=StaffCopierIdentityOut)
async def update_staff_copier_identity(
    identity_id: UUID, payload: StaffCopierIdentityUpdate, db: AsyncSession = Depends(get_db)
):
    identity = await _get_identity_or_404(identity_id, db)
    updates = payload.model_dump(exclude_unset=True)

    new_type = updates.get("identity_type", identity.identity_type)
    new_value = updates.get("identity_value", identity.identity_value)
    new_device_id = updates.get("mfp_device_id", identity.mfp_device_id)
    if (
        new_type != identity.identity_type
        or new_value != identity.identity_value
        or new_device_id != identity.mfp_device_id
    ):
        await _check_duplicate(db, new_type, new_value, new_device_id, exclude_id=identity.id)

    for field, value in updates.items():
        setattr(identity, field, value)

    await db.commit()
    await db.refresh(identity)
    return identity


@router.delete("/{identity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_copier_identity(identity_id: UUID, db: AsyncSession = Depends(get_db)):
    identity = await _get_identity_or_404(identity_id, db)
    await db.delete(identity)
    await db.commit()
