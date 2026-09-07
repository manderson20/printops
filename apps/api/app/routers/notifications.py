from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.record import diff, record_audit, snapshot
from app.db import get_db
from app.deps import get_current_user, require_role
from app.mail.settings import get_or_create_smtp_settings
from app.models.notification import NotificationChannel, NotificationEvent
from app.notifications.delivery import DeliveryError, send_to_channel
from app.notifications.settings import get_or_create_notification_settings
from app.schemas.auth import UserOut
from app.schemas.notification import (
    NotificationChannelCreate,
    NotificationChannelOut,
    NotificationChannelUpdate,
    NotificationEventPage,
    NotificationSettingsOut,
    NotificationSettingsUpdate,
    channel_out,
    event_out,
    validate_target,
)

# Admin-only: the channel list is a set of credentials, and the event feed says
# which printers are broken and who is over quota.
router = APIRouter(dependencies=[Depends(require_role("admin"))])

AUDITED_SETTINGS_FIELDS = [
    "enabled",
    "settle_minutes",
    "toner_low_percent",
    "notify_printer_attention",
    "notify_printer_error",
    "notify_toner_low",
    "notify_quota_exceeded",
]


@router.get("/settings", response_model=NotificationSettingsOut)
async def get_notification_settings(db: AsyncSession = Depends(get_db)):
    return NotificationSettingsOut.model_validate(await get_or_create_notification_settings(db))


@router.put("/settings", response_model=NotificationSettingsOut)
async def update_notification_settings(
    payload: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
    request: Request = None,
):
    settings = await get_or_create_notification_settings(db)
    before = snapshot(settings, AUDITED_SETTINGS_FIELDS)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(settings, field, value)

    changes = diff(before, snapshot(settings, AUDITED_SETTINGS_FIELDS), AUDITED_SETTINGS_FIELDS)
    if changes:
        record_audit(
            db,
            current_user,
            action="settings.notifications.update",
            summary="Updated notification settings",
            entity_type="settings.notifications",
            changes=changes,
            request=request,
        )
        await db.commit()
        await db.refresh(settings)
    return NotificationSettingsOut.model_validate(settings)


@router.get("/channels", response_model=list[NotificationChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)):
    rows = (
        (await db.execute(select(NotificationChannel).order_by(NotificationChannel.name)))
        .scalars()
        .all()
    )
    return [channel_out(row) for row in rows]


@router.post(
    "/channels", response_model=NotificationChannelOut, status_code=status.HTTP_201_CREATED
)
async def create_channel(
    payload: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
    request: Request = None,
):
    channel = NotificationChannel(
        kind=payload.kind, name=payload.name, target=payload.target, enabled=payload.enabled
    )
    db.add(channel)
    await db.flush()
    record_audit(
        db,
        current_user,
        action="notification_channel.create",
        summary=f"Added the notification channel {channel.name}",
        entity_type="notification_channel",
        entity_id=channel.id,
        entity_label=channel.name,
        # The URL itself never enters the audit trail — it is a credential, and
        # an audit row carrying one is a credential store with a search box.
        changes={"target": {"from": None, "to": "***"}},
        request=request,
    )
    await db.commit()
    await db.refresh(channel)
    return channel_out(channel)


@router.patch("/channels/{channel_id}", response_model=NotificationChannelOut)
async def update_channel(
    channel_id: UUID,
    payload: NotificationChannelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
    request: Request = None,
):
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    updates = payload.model_dump(exclude_unset=True)
    changes: dict = {}
    if updates.get("name") is not None and updates["name"] != channel.name:
        changes["name"] = {"from": channel.name, "to": updates["name"]}
        channel.name = updates["name"]
    if updates.get("enabled") is not None and updates["enabled"] != channel.enabled:
        changes["enabled"] = {"from": channel.enabled, "to": updates["enabled"]}
        channel.enabled = updates["enabled"]
    if updates.get("target"):
        # Revalidated against the stored kind. The schema cannot do it — an
        # update does not carry the kind — and without this a webhook channel
        # accepts an email address and fails at the first real incident.
        try:
            channel.target = validate_target(channel.kind, updates["target"])
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        # A new URL clears the recorded failure: the thing that was failing is
        # not the thing configured now, and leaving the old error there would
        # read as though the replacement had already failed too.
        channel.last_error = None
        changes["target"] = {"from": "***", "to": "***"}

    if changes:
        record_audit(
            db,
            current_user,
            action="notification_channel.update",
            summary=f"Updated the notification channel {channel.name}",
            entity_type="notification_channel",
            entity_id=channel.id,
            entity_label=channel.name,
            changes=changes,
            request=request,
        )
        await db.commit()
        await db.refresh(channel)
    return channel_out(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
    request: Request = None,
):
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    # Read before the delete; afterwards the instance is detached.
    label = channel.name
    await db.delete(channel)
    record_audit(
        db,
        current_user,
        action="notification_channel.delete",
        summary=f"Removed the notification channel {label}",
        entity_type="notification_channel",
        entity_id=channel_id,
        entity_label=label,
        request=request,
    )
    await db.commit()


@router.post("/channels/{channel_id}/test", status_code=status.HTTP_200_OK)
async def test_channel(channel_id: UUID, db: AsyncSession = Depends(get_db)):
    """Send a message now, and report what happened.

    Not audited: it changes nothing, and an admin pressing Test three times
    while sorting out a URL should not fill the trail. The point is that
    somebody setting this up finds out immediately rather than discovering at
    the first real incident that the URL was wrong.
    """
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    probe = NotificationEvent(
        kind="test",
        dedupe_key=f"test:{channel_id}",
        title="PrintOps test message",
        body="If you can read this, PrintOps can reach this channel.",
        severity="info",
        created_at=datetime.now(UTC),
    )
    try:
        # An email channel needs the relay settings; without them send_mail
        # refuses with "Email is not configured" and the Test button reports a
        # failure that is really this endpoint's, not the relay's.
        smtp = await get_or_create_smtp_settings(db) if channel.kind == "email" else None
        await send_to_channel(channel, probe, smtp)
    except DeliveryError as exc:
        channel.last_error = str(exc)[:500]
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    channel.last_error = None
    channel.last_success_at = datetime.now(UTC)
    await db.commit()
    return {"ok": True}


@router.get("/events", response_model=NotificationEventPage)
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Recent notifications, newest first — what was sent, and what failed.

    The page an admin looks at when they suspect they are not being told
    things: an event here with a `last_error` says the detection worked and the
    delivery did not, which is a different problem from silence.
    """
    total = (await db.execute(select(func.count(NotificationEvent.id)))).scalar_one()
    rows = (
        (
            await db.execute(
                select(NotificationEvent).order_by(NotificationEvent.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return NotificationEventPage(events=[event_out(row) for row in rows], total=total)
