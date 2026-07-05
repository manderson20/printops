"""Unmapped copier activity — CopierUsageRecord rows whose raw identity
(badge/staff ID/department code/...) didn't match any known
StaffCopierIdentity at import time. Mirrors app/routers/device_overrides.py's
"unknown identity -> admin assigns -> backfill already-logged rows"
pattern exactly, just for copier usage instead of print jobs."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.copier_import import CopierImportBatch
from app.models.copier_usage import CopierUsageRecord
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.staff_copier_identity import StaffCopierIdentity
from app.schemas.copier_unmapped import (
    ResolveUnmappedOut,
    ResolveUnmappedRequest,
    UnmappedIdentityGroupOut,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[UnmappedIdentityGroupOut])
async def list_unmapped_copier_activity(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            CopierUsageRecord.mfp_device_id,
            CopierUsageRecord.external_identity_used,
            func.count(CopierUsageRecord.id),
            func.min(CopierUsageRecord.created_at),
            func.max(CopierUsageRecord.created_at),
        )
        .where(CopierUsageRecord.staff_email.is_(None))
        .group_by(CopierUsageRecord.mfp_device_id, CopierUsageRecord.external_identity_used)
        .order_by(func.count(CopierUsageRecord.id).desc())
    )
    groups = (await db.execute(stmt)).all()

    results: list[UnmappedIdentityGroupOut] = []
    for mfp_device_id, identity_used, count, first_seen, last_seen in groups:
        sample = (
            await db.execute(
                select(CopierUsageRecord)
                .where(
                    CopierUsageRecord.mfp_device_id == mfp_device_id,
                    CopierUsageRecord.external_identity_used == identity_used,
                    CopierUsageRecord.staff_email.is_(None),
                )
                .order_by(CopierUsageRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one()

        attempted_identity_type = None
        if sample.import_batch_id is not None:
            batch = await db.get(CopierImportBatch, sample.import_batch_id)
            attempted_identity_type = batch.identity_type if batch else None

        results.append(
            UnmappedIdentityGroupOut(
                mfp_device_id=mfp_device_id,
                external_identity_used=identity_used,
                occurrence_count=count,
                first_seen=first_seen,
                last_seen=last_seen,
                attempted_identity_type=attempted_identity_type,
                sample_raw_payload=sample.raw_payload,
            )
        )
    return results


@router.put("/resolve", response_model=ResolveUnmappedOut)
async def resolve_unmapped_copier_activity(
    payload: ResolveUnmappedRequest, db: AsyncSession = Depends(get_db)
):
    email = payload.resolved_email.strip().lower()
    roster_match = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email)
    )
    roster_user = roster_match.scalar_one_or_none()
    if roster_user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{payload.resolved_email}' is not in the synced Google Workspace user "
                "roster — sync Google Workspace settings, or double-check the address."
            ),
        )

    existing = await db.execute(
        select(StaffCopierIdentity).where(
            StaffCopierIdentity.identity_type == payload.identity_type,
            StaffCopierIdentity.identity_value == payload.identity_value,
            StaffCopierIdentity.mfp_device_id == payload.mfp_device_id
            if payload.mfp_device_id is not None
            else StaffCopierIdentity.mfp_device_id.is_(None),
        )
    )
    identity = existing.scalar_one_or_none()
    if identity is None:
        identity = StaffCopierIdentity(
            staff_email=email,
            identity_type=payload.identity_type,
            identity_value=payload.identity_value,
            mfp_device_id=payload.mfp_device_id,
            note=payload.note,
        )
        db.add(identity)
    else:
        identity.staff_email = email
        identity.note = payload.note

    # An org-wide identity (mfp_device_id is None) backfills every device's
    # matching unmapped rows; a device-scoped one only backfills that
    # device's — matches the identity's own resolution scope in
    # app/routers/copier_imports.py:_resolve_identity.
    backfill_stmt = (
        update(CopierUsageRecord)
        .where(
            CopierUsageRecord.external_identity_used == payload.identity_value,
            CopierUsageRecord.staff_email.is_(None),
        )
        .values(
            staff_email=email,
            staff_employee_id=roster_user.employee_id,
            external_identity_type=payload.identity_type,
        )
    )
    if payload.mfp_device_id is not None:
        backfill_stmt = backfill_stmt.where(CopierUsageRecord.mfp_device_id == payload.mfp_device_id)
    backfill_result = await db.execute(backfill_stmt)

    await db.commit()

    return ResolveUnmappedOut(
        resolved_email=email,
        identity_type=payload.identity_type,
        identity_value=payload.identity_value,
        mfp_device_id=payload.mfp_device_id,
        backfilled_row_count=backfill_result.rowcount or 0,
    )
