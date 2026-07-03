import json
import time
from datetime import UTC, datetime

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models.google_workspace import GoogleWorkspaceDevice, GoogleWorkspaceSettings, GoogleWorkspaceUser

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
        per-device recentUsers snapshot."""
        users: list[dict] = []
        page_token: str | None = None
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            access_token = await self._get_access_token(client)
            for _ in range(MAX_PAGES):
                params: dict = {"customer": self.customer_id, "maxResults": 500}
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


async def sync_users(db: AsyncSession) -> int:
    """Refreshes the local GoogleWorkspaceUser cache — the canonical email
    roster used to validate device overrides and disambiguate bare local
    usernames (app/attribution/resolve.py). Raises GoogleWorkspaceError on
    failure without touching last_sync_error itself — see run_sync()."""
    settings = await get_settings(db)
    if settings is None or not settings.enabled:
        raise GoogleWorkspaceError("Google Workspace integration is not configured/enabled.")

    client = _client_from_settings(settings)
    users = await client.list_users()

    now = datetime.now(UTC)
    await db.execute(delete(GoogleWorkspaceUser))
    count = 0
    for user in users:
        email = user.get("primaryEmail")
        if not email:
            continue
        db.add(
            GoogleWorkspaceUser(
                email=email.lower(),
                name=(user.get("name") or {}).get("fullName"),
                synced_at=now,
            )
        )
        count += 1
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
