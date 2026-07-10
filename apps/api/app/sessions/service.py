from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import SessionSettings


async def get_or_create_session_settings(db: AsyncSession) -> SessionSettings:
    result = await db.execute(select(SessionSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = SessionSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
