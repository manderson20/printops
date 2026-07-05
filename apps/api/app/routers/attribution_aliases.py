"""Manual identity merging for print-job attribution — maps an arbitrary
login string (a bare local username like "matt", or an old/alternate
email) to one canonical staff email. Mirrors app/routers/device_overrides.py's
validate-against-roster + immediate-backfill pattern exactly, just keyed
by an identity string instead of a MAC address. Google Workspace's own
account aliases populate the same table automatically
(source="google_workspace_sync" — app/integrations/google_workspace.py);
this router only ever creates/deletes source="manual" rows."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.attribution_alias import AttributionAlias
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.schemas.attribution_alias import AttributionAliasCreate, AttributionAliasOut

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[AttributionAliasOut])
async def list_attribution_aliases(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AttributionAlias).order_by(AttributionAlias.alias))
    return [
        AttributionAliasOut(
            id=a.id,
            alias=a.alias,
            resolved_email=a.resolved_email,
            source=a.source,
            note=a.note,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in result.scalars().all()
    ]


@router.post("", response_model=AttributionAliasOut, status_code=status.HTTP_201_CREATED)
async def create_attribution_alias(payload: AttributionAliasCreate, db: AsyncSession = Depends(get_db)):
    alias_key = payload.alias.strip().lower()
    email = payload.resolved_email.strip().lower()
    if not alias_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Alias can't be blank.")

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

    existing = await db.execute(select(AttributionAlias).where(AttributionAlias.alias == alias_key))
    alias_row = existing.scalar_one_or_none()
    if alias_row is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{payload.alias}' is already mapped to {alias_row.resolved_email}.",
        )

    alias_row = AttributionAlias(alias=alias_key, resolved_email=email, source="manual", note=payload.note)
    db.add(alias_row)

    backfill_result = await db.execute(
        update(Job)
        .where(func.lower(Job.submitted_by) == alias_key)
        .values(submitted_by=email, attribution_method="alias")
    )

    await db.commit()
    await db.refresh(alias_row)
    return AttributionAliasOut(
        id=alias_row.id,
        alias=alias_row.alias,
        resolved_email=alias_row.resolved_email,
        source=alias_row.source,
        note=alias_row.note,
        created_at=alias_row.created_at,
        updated_at=alias_row.updated_at,
        backfilled_job_count=backfill_result.rowcount or 0,
    )


@router.delete("/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribution_alias(alias_id: UUID, db: AsyncSession = Depends(get_db)):
    alias_row = await db.get(AttributionAlias, alias_id)
    if alias_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alias not found")
    await db.delete(alias_row)
    await db.commit()
