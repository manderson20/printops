from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditSettings


async def get_or_create_audit_settings(db: AsyncSession) -> AuditSettings:
    """The singleton, created lazily on first read — same shape and same reason
    as app/syslog/service.py's get_or_create_syslog_settings.

    Shared between the router and the purge loop (app/main.py) rather than each
    holding its own copy, so the retention default is written down once. Two
    places defaulting the same number is how they come to disagree.
    """
    settings = (await db.execute(select(AuditSettings).limit(1))).scalar_one_or_none()
    if settings is None:
        settings = AuditSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
