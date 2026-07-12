from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.server_settings import ServerSettings


async def get_or_create_server_settings(db: AsyncSession) -> ServerSettings:
    """Singleton — one row. Shared by the admin settings router
    (app/routers/settings.py) and the internal backend-token router
    (app/routers/internal.py, read by scripts/sync_server_settings.sh),
    same "public getter in its own service module" shape as
    get_or_create_ldap_relay_settings/get_or_create_quota_settings."""
    result = await db.execute(select(ServerSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        # Seeded from the env-configured default (app/core/config.py) so a
        # fresh install starts from whatever's already working, rather than
        # an empty hostname — same "seed from a confirmed-working default"
        # reasoning as SnmpDefaultsSettings.community_encrypted.
        settings = ServerSettings(hostname=get_settings().print_server_host)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
