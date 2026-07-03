import ipaddress
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.classguard import ClassGuardError, client_from_settings, get_settings
from app.integrations.mosyle import normalize_mac as normalize_mac_mosyle
from app.models.device_override import DeviceUserOverride
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.mosyle import MosyleDevice, MosyleSettings

logger = logging.getLogger(__name__)

GENERIC_CUPS_USERS = {"", "anonymous"}


async def _lookup_mac_for_source(db: AsyncSession, source_host: str) -> str | None:
    """Resolves a client's IP (from CUPS's job-originating-host-name) to a
    MAC address via ClassGuard's DHCP lease table, so it can be matched
    against Mosyle's/Google Workspace's cached device lists (neither
    exposes IP directly — see app/integrations/mosyle.py and
    app/integrations/google_workspace.py). The print server itself can't
    do this lookup locally: it and printing clients sit on different
    subnets (confirmed 2026-07-02), so the print server's own ARP table
    never has an entry for a client's IP — ClassGuard sits on the actual
    subnets and has a real DHCP-lease-backed IP<->MAC view.

    Never raises — a ClassGuard outage/misconfiguration must not block
    logging a print job; it just means MAC-based strategies don't resolve
    for this job, same as if ClassGuard were disabled."""
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


async def _integration_enabled(db: AsyncSession, model) -> bool:
    result = await db.execute(select(model.enabled).limit(1))
    return bool(result.scalar_one_or_none())


def _looks_like_email(value: str) -> bool:
    return "@" in value


async def _find_roster_user_by_email(db: AsyncSession, email: str) -> GoogleWorkspaceUser | None:
    result = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email.strip().lower())
    )
    return result.scalar_one_or_none()


async def _find_roster_user_by_local_part(db: AsyncSession, username: str) -> GoogleWorkspaceUser | None:
    """Best-effort reconciliation for a Mosyle-reported username that
    doesn't show up verbatim as a roster email — Mosyle's own `username`
    field is often just the mailbox local part (e.g. "jdoe"), distinct
    from its separately-reported `useremail` (see
    app/integrations/mosyle.py's sync_devices). Matches the roster user
    whose email's local part equals the given username, case-insensitively.

    Ambiguous matches (two roster users whose emails share that local part
    under different domains) are dropped rather than guessed — same
    principle as the ambiguous-MAC handling in
    app/integrations/mosyle.py's sync_devices: a mis-attribution is worse
    than no attribution."""
    key = username.strip().lower()
    if not key:
        return None
    result = await db.execute(select(GoogleWorkspaceUser))
    matches = [u for u in result.scalars().all() if u.email.split("@", 1)[0].lower() == key]
    return matches[0] if len(matches) == 1 else None


async def resolve_user(
    db: AsyncSession, cups_user: str | None, source_host: str | None
) -> tuple[str, str, str | None]:
    """Ordered attribution strategy chain — ARCHITECTURE.md §4, strategies
    1-3 (the unknown-user secure hold queue, strategy 4, is later work,
    out of scope for this pass). Never raises; always resolves to
    *something*, per §4's "no job is ever silently mis-attributed."

    A bare, non-email-shaped CUPS username (e.g. a local macOS account
    name like "matt") is *not* trusted immediately — two different people
    can share the same short local username, so it's only used as a
    last-resort fallback after MAC-based resolution (admin override, then
    Mosyle, then Google Workspace) has had a chance to disambiguate via
    the specific physical device. An already-email-shaped CUPS username is
    assumed unambiguous and still wins outright, same as before.

    A Mosyle-resolved device is additionally reconciled against the
    Google Workspace roster (see _find_roster_user_by_email/
    _find_roster_user_by_local_part above) before being trusted outright,
    since Mosyle's own reported email can be a stale alias that no longer
    matches the canonical Workspace address.

    Returns (attributed_user, method, mac_address) where method is one of
    "cups" / "override" / "mosyle" / "google_workspace" / "unresolved", and
    mac_address is whatever ClassGuard resolved for source_host (or None),
    independent of whether that MAC resolved to a user — callers persist
    it on the Job row so a later admin override can backfill this specific
    job (see app/routers/device_overrides.py)."""
    cups_user_clean = cups_user if cups_user and cups_user.strip().lower() not in GENERIC_CUPS_USERS else None

    if cups_user_clean and _looks_like_email(cups_user_clean):
        return cups_user_clean, "cups", None

    mac: str | None = None
    if source_host:
        raw_mac = await _lookup_mac_for_source(db, source_host)
        # Mosyle and Google Workspace's normalizers are identical (upper-case,
        # colon-separated); normalize once here so DeviceUserOverride, the
        # Job.mac_address column, and both device caches all compare the
        # same canonical form.
        mac = normalize_mac_mosyle(raw_mac) if raw_mac else None

    if mac:
        result = await db.execute(select(DeviceUserOverride).where(DeviceUserOverride.mac_address == mac))
        override = result.scalar_one_or_none()
        if override:
            return override.resolved_email, "override", mac

        if await _integration_enabled(db, MosyleSettings):
            result = await db.execute(select(MosyleDevice).where(MosyleDevice.mac_address == mac))
            device = result.scalar_one_or_none()
            if device and (device.user_email or device.user_name):
                # Mosyle's own reported email can be a stale alias or
                # otherwise not match the canonical Workspace address (the
                # roster is the ground truth used for override validation
                # and the usage report — app/routers/device_overrides.py,
                # app/routers/jobs.py). Reconcile: prefer the roster's
                # confirmed email, falling back to matching Mosyle's
                # separately-reported username against the roster, and
                # only trusting Mosyle's raw email outright if neither
                # confirms it (e.g. Google Workspace isn't set up at all).
                roster_match = (
                    await _find_roster_user_by_email(db, device.user_email)
                    if device.user_email
                    else None
                )
                if roster_match is None and device.user_name:
                    roster_match = await _find_roster_user_by_local_part(db, device.user_name)
                if roster_match:
                    return roster_match.email, "mosyle", mac
                if device.user_email:
                    return device.user_email, "mosyle", mac

        if await _integration_enabled(db, GoogleWorkspaceSettings):
            result = await db.execute(select(GoogleWorkspaceDevice).where(GoogleWorkspaceDevice.mac_address == mac))
            device = result.scalar_one_or_none()
            if device and device.user_email:
                return device.user_email, "google_workspace", mac

    if cups_user_clean:
        return cups_user_clean, "cups", mac

    return cups_user or "unknown", "unresolved", mac
