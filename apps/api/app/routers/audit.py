from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.record import record_audit
from app.audit.settings import get_or_create_audit_settings
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.audit import AuditEvent
from app.schemas.audit import (
    AuditActionsOut,
    AuditEventOut,
    AuditEventPage,
    AuditSettingsOut,
    AuditSettingsUpdate,
)
from app.schemas.auth import UserOut

# Admin-only for the whole router, same as syslog. The log records what admins
# did to the system, including to each other's accounts — a viewer having read
# access to it would be handing out an activity feed of the people above them.
router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=AuditEventPage)
async def list_audit_events(
    actor: str | None = Query(None),
    action_prefix: str | None = Query(None),
    entity_type: str | None = Query(None),
    search: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """The admin action log, newest first.

    `action_prefix` matches on the dotted hierarchy — "settings" catches
    everything under it — which is why actions are named that way rather than
    carrying a separate category column.

    Reading the log is deliberately not itself audited. Every list request would
    write a row, so an admin leaving the page open would fill the table with
    records of themselves looking at it, and the actions worth finding would be
    buried under the act of looking for them.
    """
    query = select(AuditEvent)
    count_query = select(func.count()).select_from(AuditEvent)

    filters = []
    if actor:
        filters.append(AuditEvent.actor_email.ilike(f"%{actor}%"))
    if action_prefix:
        # Prefix, not substring: "settings" should mean the settings tree, not
        # anything with the word in it.
        filters.append(
            or_(AuditEvent.action == action_prefix, AuditEvent.action.like(f"{action_prefix}.%"))
        )
    if entity_type:
        filters.append(AuditEvent.entity_type == entity_type)
    if search:
        needle = f"%{search}%"
        filters.append(
            or_(
                AuditEvent.summary.ilike(needle),
                AuditEvent.entity_label.ilike(needle),
                AuditEvent.actor_email.ilike(needle),
            )
        )
    if since:
        filters.append(AuditEvent.occurred_at >= since)
    if until:
        filters.append(AuditEvent.occurred_at <= until)

    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = (await db.execute(count_query)).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return AuditEventPage(
        events=[AuditEventOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/actions", response_model=AuditActionsOut)
async def list_audit_actions(db: AsyncSession = Depends(get_db)):
    """Distinct actions present in the log, for the UI's filter dropdown."""
    rows = (
        (await db.execute(select(AuditEvent.action).distinct().order_by(AuditEvent.action)))
        .scalars()
        .all()
    )
    return AuditActionsOut(actions=list(rows))


@router.get("/settings", response_model=AuditSettingsOut)
async def get_audit_settings(db: AsyncSession = Depends(get_db)):
    return AuditSettingsOut.model_validate(await get_or_create_audit_settings(db))


@router.put("/settings", response_model=AuditSettingsOut)
async def update_audit_settings(
    payload: AuditSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Changing how long the trail is kept is itself recorded in the trail.

    Shortening retention is the one setting change that can destroy audit
    history, so it is the one that most needs a record of who did it — and
    because the record is written in the same transaction, the change cannot
    land without it.
    """
    settings = await get_or_create_audit_settings(db)
    before = settings.retention_days
    if payload.retention_days != before:
        settings.retention_days = payload.retention_days
        record_audit(
            db,
            current_user,
            action="settings.audit.update",
            summary=(f"Changed audit retention from {before} to {payload.retention_days} days"),
            entity_type="settings.audit",
            changes={"retention_days": {"from": before, "to": payload.retention_days}},
        )
        await db.commit()
        await db.refresh(settings)
    return AuditSettingsOut.model_validate(settings)
