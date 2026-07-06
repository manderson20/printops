from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ldap_relay import LdapRelaySettings


async def get_or_create_ldap_relay_settings(db: AsyncSession) -> LdapRelaySettings:
    result = await db.execute(select(LdapRelaySettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = LdapRelaySettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
