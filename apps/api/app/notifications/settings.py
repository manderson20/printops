from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import NotificationSettings


async def get_or_create_notification_settings(db: AsyncSession) -> NotificationSettings:
    """The singleton, created lazily on first read — same shape and reason as
    app/syslog/service.py's and app/audit/settings.py's.

    Shared between the router, the poll loops that raise conditions, and the
    delivery loop, so the defaults are written down once. Two places defaulting
    the same number is how they come to disagree.
    """
    settings = (await db.execute(select(NotificationSettings).limit(1))).scalar_one_or_none()
    if settings is None:
        settings = NotificationSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
