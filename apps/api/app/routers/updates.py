from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_role, verify_backend_token
from app.integrations.git_update import GitUpdateError, get_changelog_section, get_current_version, get_latest_version
from app.models.update_schedule import UpdateSchedule
from app.schemas.auth import UserOut
from app.schemas.update_schedule import (
    ScheduleUpdateIn,
    UpdateCheckOut,
    UpdateCompleteIn,
    UpdateScheduleOut,
    UpdateStatusOut,
    VersionOut,
)

router = APIRouter()

# A schedule row in either of these statuses is "the" active one — at most
# one at a time (see app/models/update_schedule.py).
ACTIVE_STATUSES = ("pending", "in_progress")


@router.get("/version", response_model=VersionOut)
async def version(current_user: UserOut = Depends(get_current_user)):
    """Cheap, local-file read — shown to every logged-in user (e.g. a small
    badge in the dashboard layout), unlike /check below which does a live
    git fetch and is admin-only."""
    return VersionOut(version=get_current_version())


@router.get("/check", response_model=UpdateCheckOut, dependencies=[Depends(require_role("admin"))])
async def check_for_update():
    current = get_current_version()
    try:
        latest = get_latest_version()
    except GitUpdateError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    update_available = latest != current
    changelog = get_changelog_section(latest) if update_available else None
    return UpdateCheckOut(
        current_version=current,
        latest_version=latest,
        update_available=update_available,
        changelog=changelog,
    )


@router.get(
    "/schedule",
    response_model=list[UpdateScheduleOut],
    dependencies=[Depends(require_role("admin"))],
)
async def list_schedule(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UpdateSchedule).order_by(UpdateSchedule.created_at.desc()).limit(20)
    )
    return result.scalars().all()


@router.post("/schedule", response_model=UpdateScheduleOut, status_code=status.HTTP_201_CREATED)
async def schedule_update(
    payload: ScheduleUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(require_role("admin")),
):
    result = await db.execute(select(UpdateSchedule).where(UpdateSchedule.status.in_(ACTIVE_STATUSES)))
    existing = result.scalar_one_or_none()
    if existing is not None:
        if existing.status == "in_progress":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An update is already in progress — wait for it to finish.",
            )
        existing.target_version = payload.target_version
        existing.scheduled_at = payload.scheduled_at
        existing.requested_by = current_user.username
        row = existing
    else:
        row = UpdateSchedule(
            target_version=payload.target_version,
            scheduled_at=payload.scheduled_at,
            requested_by=current_user.username,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/schedule/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def cancel_schedule(schedule_id: UUID, db: AsyncSession = Depends(get_db)):
    row = await db.get(UpdateSchedule, schedule_id)
    if row is None or row.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active scheduled update with that id.",
        )
    row.status = "failed"
    row.log = "Cancelled by admin."
    row.completed_at = datetime.now(UTC)
    await db.commit()


@router.get("/status", response_model=UpdateStatusOut, dependencies=[Depends(verify_backend_token)])
async def update_status(db: AsyncSession = Depends(get_db)):
    """Polled once a minute by the host-level update-watcher (see
    infra/update-watcher/update-watcher.sh) — same X-Backend-Token trust
    boundary as the CUPS backend script (app/deps.py's
    verify_backend_token), not a user session."""
    result = await db.execute(
        select(UpdateSchedule)
        .where(UpdateSchedule.status.in_(ACTIVE_STATUSES))
        .order_by(UpdateSchedule.scheduled_at)
        .limit(1)
    )
    return UpdateStatusOut(pending=result.scalar_one_or_none())


@router.post(
    "/complete", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_backend_token)]
)
async def complete_update(payload: UpdateCompleteIn, db: AsyncSession = Depends(get_db)):
    """Called by the same update-watcher, once when it starts running
    apply-update.sh (status="in_progress") and again with the final
    result — never raises on "nothing pending" so a stray/duplicate call
    from the watcher is harmless."""
    result = await db.execute(
        select(UpdateSchedule)
        .where(UpdateSchedule.status.in_(ACTIVE_STATUSES))
        .order_by(UpdateSchedule.scheduled_at)
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return
    row.status = payload.status
    row.log = payload.log
    if payload.status in ("completed", "failed"):
        row.completed_at = datetime.now(UTC)
    await db.commit()
