import ipaddress

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.mosyle import normalize_mac
from app.models.mosyle import MosyleDevice, MosyleSettings

GENERIC_CUPS_USERS = {"", "anonymous"}


def _lookup_mac_for_source(source_host: str) -> str | None:
    """Resolves a client's IP (from CUPS's job-originating-host-name) to a
    MAC address, so it can be matched against Mosyle's cached device list
    (which only exposes MAC, not IP — see app/integrations/mosyle.py).

    NOT IMPLEMENTED YET: the print server (172.16.2.10/21) and printing
    clients sit on different subnets, routed via a gateway — confirmed
    2026-07-02 (a real job came from 10.20.1.67, not an ARP-visible L2
    neighbor of this box). The print server's own ARP table can't see
    cross-subnet clients, so a local `ip neighbor` lookup was ruled out.

    The intended fix is an integration with ClassGuard (the org's own
    DHCP/DNS/web-filter platform, which sits on the actual subnets and has
    a real IP<->MAC view) — not yet wired in. This is the single seam to
    implement: given a source IP, return its MAC address (colon-separated
    hex, any case — normalize_mac() in app/integrations/mosyle.py handles
    formatting) or None if unknown. Until this returns real data, strategy
    2 below never resolves and every job falls through to strategy 1 (raw
    CUPS attribution) or "unresolved" — the same behavior as before this
    module existed, not a regression.
    """
    try:
        ipaddress.ip_address(source_host)
    except ValueError:
        return None  # hostname, not an IP literal — nothing to look up yet either way

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
        mac = _lookup_mac_for_source(source_host)
        if mac:
            result = await db.execute(
                select(MosyleDevice).where(MosyleDevice.mac_address == normalize_mac(mac))
            )
            device = result.scalar_one_or_none()
            if device and device.user_email:
                return device.user_email, "mosyle"

    return cups_user or "unknown", "unresolved"
