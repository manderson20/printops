import json
import time
from datetime import UTC, datetime

import httpx
import jwt
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models.attribution_alias import AttributionAlias
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.job import Job
from app.models.staff_copier_identity import StaffCopierIdentity

REQUEST_TIMEOUT_SECONDS = 30
TOKEN_URL = "https://oauth2.googleapis.com/token"
DIRECTORY_API_BASE = "https://admin.googleapis.com/admin/directory/v1"
# Space-separated per OAuth2 scope syntax — device inventory (existing) plus
# read-only access to the full user directory, needed to build a canonical
# email roster for attribution reconciliation (app/attribution/resolve.py).
SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.device.chromeos.readonly "
    "https://www.googleapis.com/auth/admin.directory.user.readonly"
)
# OAuth access tokens from this flow are short-lived (~1h); refresh a
# little early rather than racing expiry mid-sync.
JWT_LIFETIME_SECONDS = 3600
MAX_PAGES = 50


class GoogleWorkspaceError(Exception):
    pass


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


def extract_employee_id(user: dict) -> str | None:
    """Google's built-in Employee ID field is exposed as an entry in the
    user's `externalIds` array with `type == "organization"` — only
    present in the API response when the request asks for the "full"
    projection (see list_users below); the default "basic" projection
    omits it entirely, not just leaves it null. Feeds the copier PIN
    roster export (app/routers/settings.py)."""
    for external_id in user.get("externalIds") or []:
        if external_id.get("type") == "organization" and external_id.get("value"):
            return external_id["value"]
    return None


def normalize_org_unit_path(path: str) -> str:
    """"/Employees", "/Employees/", " /Employees " all mean the same OU —
    normalize to a leading slash, no trailing slash, so a saved setting
    and a value read back off a synced user compare consistently."""
    trimmed = path.strip()
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    return trimmed.rstrip("/") or "/"


def org_unit_matches(user_org_unit_path: str | None, configured_org_unit_path: str) -> bool:
    """True if a user's OU is the configured one or nested under it — every
    org names/structures this differently ("/Employees", "/Staff",
    "/Personnel/Certified", possibly with sub-OUs like "/Employees/Teachers"),
    so this is never hardcoded (see GoogleWorkspaceSettings.staff_org_unit_path)."""
    if not user_org_unit_path:
        return False
    target = normalize_org_unit_path(configured_org_unit_path)
    user_path = normalize_org_unit_path(user_org_unit_path)
    return user_path == target or user_path.startswith(f"{target}/")


class GoogleWorkspaceClient:
    """Client for the Admin SDK Directory API, reading ChromeOS device
    inventory. Auth is a service-account + domain-wide delegation flow —
    officially documented by Google (unlike Mosyle's), so this follows
    the standard JWT-bearer OAuth2 pattern directly rather than through
    Google's own client libraries, to stay consistent with how the other
    integrations here are built (thin httpx wrappers, no vendor SDKs):

    1. Build a JWT asserting this service account (`iss`, from the JSON
       key's client_email), scoped to the ChromeOS read-only scope, with
       `sub` set to the Workspace admin email being impersonated —
       required for domain-wide delegation to actually apply.
    2. Sign it RS256 with the service account's private key (from the
       same JSON key) and POST it to Google's token endpoint
       (grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer) to get a
       short-lived OAuth access token.
    3. Use that as a normal `Authorization: Bearer` header against
       admin.googleapis.com.

    Device records already embed the assigned user (`recentUsers[].email`)
    directly — no separate per-user API call needed, same as Mosyle."""

    def __init__(self, service_account_json: str, admin_email: str, customer_id: str = "my_customer"):
        try:
            self._key_data = json.loads(service_account_json)
        except ValueError as exc:
            raise GoogleWorkspaceError("Service account JSON is not valid JSON.") from exc
        for field in ("client_email", "private_key"):
            if field not in self._key_data:
                raise GoogleWorkspaceError(f"Service account JSON is missing '{field}'.")
        self.admin_email = admin_email
        self.customer_id = customer_id

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._key_data["client_email"],
                "sub": self.admin_email,
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + JWT_LIFETIME_SECONDS,
            },
            self._key_data["private_key"],
            algorithm="RS256",
        )

        try:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        except httpx.HTTPError as exc:
            raise GoogleWorkspaceError(f"Could not reach Google's token endpoint: {exc}") from exc

        if response.status_code != 200:
            raise GoogleWorkspaceError(
                f"Google token exchange returned HTTP {response.status_code}: {response.text[:300]}"
            )
        access_token = response.json().get("access_token")
        if not access_token:
            raise GoogleWorkspaceError("Google token exchange succeeded but returned no access_token.")
        return access_token

    async def list_chromeos_devices(self) -> list[dict]:
        devices: list[dict] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            access_token = await self._get_access_token(client)
            for _ in range(MAX_PAGES):
                params = {"maxResults": 200}
                if page_token:
                    params["pageToken"] = page_token
                try:
                    response = await client.get(
                        f"{DIRECTORY_API_BASE}/customer/{self.customer_id}/devices/chromeos",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=params,
                    )
                except httpx.HTTPError as exc:
                    raise GoogleWorkspaceError(f"Could not reach Google's Directory API: {exc}") from exc

                if response.status_code != 200:
                    raise GoogleWorkspaceError(
                        f"Google Directory API returned HTTP {response.status_code}: {response.text[:300]}"
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise GoogleWorkspaceError(
                        f"Google Directory API returned non-JSON response: {response.text[:300]}"
                    ) from exc

                devices.extend(data.get("chromeosdevices", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return devices

    async def list_users(self) -> list[dict]:
        """Full Workspace user directory (not just device-assigned users) —
        the canonical identity roster, distinct from list_chromeos_devices'
        per-device recentUsers snapshot.

        projection=full (vs the "basic" default) is required to get
        externalIds back at all — Employee ID lives there (see
        extract_employee_id) and is silently omitted under "basic", not
        just null."""
        users: list[dict] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            access_token = await self._get_access_token(client)
            for _ in range(MAX_PAGES):
                params: dict = {
                    "customer": self.customer_id,
                    "maxResults": 500,
                    "projection": "full",
                }
                if page_token:
                    params["pageToken"] = page_token
                try:
                    response = await client.get(
                        f"{DIRECTORY_API_BASE}/users",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=params,
                    )
                except httpx.HTTPError as exc:
                    raise GoogleWorkspaceError(f"Could not reach Google's Directory API: {exc}") from exc

                if response.status_code != 200:
                    raise GoogleWorkspaceError(
                        f"Google Directory API returned HTTP {response.status_code}: {response.text[:300]}"
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise GoogleWorkspaceError(
                        f"Google Directory API returned non-JSON response: {response.text[:300]}"
                    ) from exc

                users.extend(data.get("users", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return users


async def get_settings(db: AsyncSession) -> GoogleWorkspaceSettings | None:
    result = await db.execute(select(GoogleWorkspaceSettings).limit(1))
    return result.scalar_one_or_none()


def _client_from_settings(settings: GoogleWorkspaceSettings) -> GoogleWorkspaceClient:
    if not settings.service_account_json_encrypted or not settings.admin_email:
        raise GoogleWorkspaceError("Google Workspace service account JSON / admin email are not configured.")
    return GoogleWorkspaceClient(
        service_account_json=decrypt(settings.service_account_json_encrypted),
        admin_email=settings.admin_email,
        customer_id=settings.customer_id,
    )


async def sync_devices(db: AsyncSession) -> int:
    """Refreshes the local GoogleWorkspaceDevice cache from a live API call
    and updates sync bookkeeping on success. Raises GoogleWorkspaceError on
    failure without touching last_sync_error itself — see run_sync()."""
    settings = await get_settings(db)
    if settings is None or not settings.enabled:
        raise GoogleWorkspaceError("Google Workspace integration is not configured/enabled.")

    client = _client_from_settings(settings)
    devices = await client.list_chromeos_devices()

    # Same rationale as Mosyle: a MAC shared by more than one device can't
    # be trusted to identify one specific person, so drop it from the
    # cache entirely rather than guessing.
    by_mac: dict[str, dict] = {}
    ambiguous_macs: set[str] = set()
    for device in devices:
        raw_mac = device.get("macAddress")
        if not raw_mac:
            continue
        mac = normalize_mac(raw_mac)
        if mac in by_mac:
            ambiguous_macs.add(mac)
        else:
            by_mac[mac] = device

    now = datetime.now(UTC)
    await db.execute(delete(GoogleWorkspaceDevice))
    count = 0
    for mac, device in by_mac.items():
        if mac in ambiguous_macs:
            continue
        recent_users = device.get("recentUsers") or []
        user_email = next((u.get("email") for u in recent_users if u.get("email")), None)
        db.add(
            GoogleWorkspaceDevice(
                mac_address=mac,
                serial_number=device.get("serialNumber"),
                device_name=device.get("annotatedUser") or device.get("model"),
                user_email=user_email,
                synced_at=now,
            )
        )
        count += 1

    settings.last_synced_at = now
    settings.last_sync_error = None
    settings.device_count = count
    await db.commit()
    return count


async def _refresh_google_sourced_aliases(db: AsyncSession, users: list[dict]) -> None:
    """Mirrors each user's Google-reported account aliases
    (Directory API's `aliases` field — exactly what Google itself
    populates when an account's primary address is renamed) into
    AttributionAlias rows, so app/attribution/resolve.py resolves an old/
    alternate address to the current canonical one automatically. Full
    replace of source="google_workspace_sync" rows only — manual ones
    (an admin's own merges, e.g. a local username) are never touched.

    Only backfills already-logged Job.submitted_by rows for aliases that
    are new or changed since the last sync, not the whole set every
    cycle — this runs on the same 15-min loop as every other device sync
    (app/main.py), so re-touching unchanged rows every cycle would be
    pure waste."""
    new_aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for user in users:
        email = user.get("primaryEmail")
        if not email:
            continue
        for raw_alias in user.get("aliases") or []:
            key = raw_alias.strip().lower()
            if not key:
                continue
            if key in new_aliases and new_aliases[key] != email.lower():
                ambiguous.add(key)  # same alias claimed by two different accounts — drop, don't guess
            else:
                new_aliases[key] = email.lower()
    for key in ambiguous:
        new_aliases.pop(key, None)

    manual_result = await db.execute(select(AttributionAlias.alias).where(AttributionAlias.source == "manual"))
    manual_aliases = {row[0].lower() for row in manual_result.all()}
    new_aliases = {k: v for k, v in new_aliases.items() if k not in manual_aliases}

    old_result = await db.execute(
        select(AttributionAlias.alias, AttributionAlias.resolved_email).where(
            AttributionAlias.source == "google_workspace_sync"
        )
    )
    old_aliases = {alias: resolved_email for alias, resolved_email in old_result.all()}
    changed = {k: v for k, v in new_aliases.items() if old_aliases.get(k) != v}

    await db.execute(delete(AttributionAlias).where(AttributionAlias.source == "google_workspace_sync"))
    for alias, resolved_email in new_aliases.items():
        db.add(AttributionAlias(alias=alias, resolved_email=resolved_email, source="google_workspace_sync"))

    for alias, resolved_email in changed.items():
        await db.execute(
            update(Job)
            .where(func.lower(Job.submitted_by) == alias)
            .values(submitted_by=resolved_email, attribution_method="alias")
        )


async def _refresh_google_sourced_copier_identities(
    db: AsyncSession, users_with_employee_id: list[tuple[str, str]], settings: GoogleWorkspaceSettings
) -> None:
    """Mirrors GoogleWorkspaceUser.employee_id into a StaffCopierIdentity
    (app/models/staff_copier_identity.py) when an admin has opted in
    (auto_create_copier_identity_from_employee_id) — off by default,
    since not every district wants Employee ID doubling as a copier
    login. Full replace of source="google_workspace_sync" rows only,
    same convention as _refresh_google_sourced_aliases; if the toggle is
    off, any previously auto-created rows are removed so turning it off
    actually takes effect rather than just freezing stale rows."""
    identity_type = settings.auto_copier_identity_type

    if not settings.auto_create_copier_identity_from_employee_id:
        await db.execute(delete(StaffCopierIdentity).where(StaffCopierIdentity.source == "google_workspace_sync"))
        return

    manual_result = await db.execute(
        select(StaffCopierIdentity.identity_value, StaffCopierIdentity.staff_email).where(
            StaffCopierIdentity.source == "manual",
            StaffCopierIdentity.identity_type == identity_type,
            StaffCopierIdentity.mfp_device_id.is_(None),
        )
    )
    manual_claims = {value: email for value, email in manual_result.all()}

    await db.execute(delete(StaffCopierIdentity).where(StaffCopierIdentity.source == "google_workspace_sync"))
    for email, employee_id in users_with_employee_id:
        claimed_by = manual_claims.get(employee_id)
        if claimed_by and claimed_by.lower() != email.lower():
            continue  # an admin already manually assigned this value to someone else
        db.add(
            StaffCopierIdentity(
                staff_email=email,
                identity_type=identity_type,
                identity_value=employee_id,
                mfp_device_id=None,
                source="google_workspace_sync",
            )
        )


async def sync_users(db: AsyncSession) -> int:
    """Refreshes the local GoogleWorkspaceUser cache — the canonical email
    roster used to validate device overrides and disambiguate bare local
    usernames (app/attribution/resolve.py). Also refreshes Google-sourced
    attribution aliases and (if enabled) copier identities from Employee
    ID — see _refresh_google_sourced_aliases/
    _refresh_google_sourced_copier_identities. Raises GoogleWorkspaceError
    on failure without touching last_sync_error itself — see run_sync()."""
    settings = await get_settings(db)
    if settings is None or not settings.enabled:
        raise GoogleWorkspaceError("Google Workspace integration is not configured/enabled.")

    client = _client_from_settings(settings)
    users = await client.list_users()

    now = datetime.now(UTC)
    await db.execute(delete(GoogleWorkspaceUser))
    count = 0
    users_with_employee_id: list[tuple[str, str]] = []
    for user in users:
        email = user.get("primaryEmail")
        if not email:
            continue
        employee_id = extract_employee_id(user)
        db.add(
            GoogleWorkspaceUser(
                email=email.lower(),
                name=(user.get("name") or {}).get("fullName"),
                employee_id=employee_id,
                org_unit_path=user.get("orgUnitPath"),
                aliases=user.get("aliases"),
                synced_at=now,
            )
        )
        count += 1
        if employee_id:
            users_with_employee_id.append((email.lower(), employee_id))

    await _refresh_google_sourced_aliases(db, users)
    await _refresh_google_sourced_copier_identities(db, users_with_employee_id, settings)

    await db.commit()
    return count


async def run_sync(db: AsyncSession) -> int:
    """Wrapper for the background loop and the manual /sync endpoint —
    records failure on GoogleWorkspaceSettings.last_sync_error before
    re-raising, so a bad sync is visible in the UI instead of failing
    silently. Syncs both the device cache and the user roster — the
    latter powers attribution overrides, not just device attribution."""
    try:
        device_count = await sync_devices(db)
        await sync_users(db)
        return device_count
    except GoogleWorkspaceError as exc:
        settings = await get_settings(db)
        if settings is not None:
            settings.last_sync_error = str(exc)
            await db.commit()
        raise
