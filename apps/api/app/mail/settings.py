from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.smtp import SmtpSettings


async def get_or_create_smtp_settings(db: AsyncSession) -> SmtpSettings:
    """The singleton, created lazily on first read — same shape and reason as
    the syslog, audit and notification getters."""
    settings = (await db.execute(select(SmtpSettings).limit(1))).scalar_one_or_none()
    if settings is None:
        settings = SmtpSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
