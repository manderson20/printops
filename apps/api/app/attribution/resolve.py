import ipaddress
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.classguard import ClassGuardError, client_from_settings, get_settings
from app.integrations.mosyle import normalize_mac
from app.models.mosyle import MosyleDevice, MosyleSettings

logger = logging.getLogger(__name__)

GENERIC_CUPS_USERS = {"", "anonymous"}


async def _lookup_mac_for_source(db: AsyncSession, source_host: str) -> str | None:
    """Resolves a client's IP (from CUPS's job-originating-host-name) to a
    MAC address via ClassGuard's DHCP lease table, so it can be matched
    against Mosyle's cached device list (which only exposes MAC, not IP —
    see app/integrations/mosyle.py). The print server itself can't do this
    lookup locally: it and printing clients sit on different subnets
    (confirmed 2026-07-02), so the print server's own ARP table never has
    an entry for a client's IP — ClassGuard sits on the actual subnets and
    has a real DHCP-lease-backed IP<->MAC view.

    Never raises — a ClassGuard outage/misconfiguration must not block
    logging a print job; it just means this strategy doesn't resolve for
    this job, same as if ClassGuard were disabled."""
    try:
        ipaddress.ip_address(source_host)
    except ValueError:
        return None  # hostname, not an IP literal — nothing to look up

    settings = await get_settings(db)
    if settings is None or not settings.enabled:
        return None

    try:
        client = client_from_settings(settings)
        return await client.lookup_mac(source_host)
    except ClassGuardError as exc:
        logger.warning("ClassGuard MAC lookup failed for %s: %s", source_host, exc)
        return None


async def _mosyle_enabled(db: AsyncSession) -> bool:
    result = await db.execute(select(MosyleSettings.enabled).limit(1))
    return bool(result.scalar_one_or_none())


async def resolve_user(
    db: AsyncSession, cups_user: str | None, source_host: str | None
) -> tuple[str, str]:
    """Ordered attribution strategy chain — ARCHITECTURE.md §4, strategies 1
    and 2 only (Google Admin + the unknown-user secure hold queue are later
    work, out of scope for this pass). Never raises; always resolves to
    *something*, per §4's "no job is ever silently mis-attributed."
    Returns (attributed_user, method) where method is one of
    "cups" / "mosyle" / "unresolved"."""
    if cups_user and cups_user.strip().lower() not in GENERIC_CUPS_USERS:
        return cups_user, "cups"

    if source_host and await _mosyle_enabled(db):
        mac = await _lookup_mac_for_source(db, source_host)
        if mac:
            result = await db.execute(
                select(MosyleDevice).where(MosyleDevice.mac_address == normalize_mac(mac))
            )
            device = result.scalar_one_or_none()
            if device and device.user_email:
                return device.user_email, "mosyle"

    return cups_user or "unknown", "unresolved"
