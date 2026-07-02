import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models.mosyle import MosyleDevice, MosyleSettings

REQUEST_TIMEOUT_SECONDS = 30
# Safety net against an unexpected pagination response looping forever —
# well beyond any real school's device count per OS type.
MAX_PAGES = 50


class MosyleError(Exception):
    pass


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


class MosyleClient:
    """Client for Mosyle *Manager* (K-12 schools, managerapi.mosyle.com/v2)
    — this org's product, not Mosyle Business (a different product/host/
    API version, businessapi.mosyle.com/v1). The original implementation
    here was built against Business-flavored research (accessToken as an
    HTTP header + admin HTTP Basic auth) and failed against a real Manager
    tenant with `{"error":"accessToken Required"}` (HTTP 404). Rewritten
    2026-07-03 to match a verified working implementation
    (github.com/instipod/pymosyle) — Manager's actual flow is a two-step
    JWT exchange, not a single authenticated request:

    1. POST {base_url}/login with {"accessToken", "email", "password"} in
       the JSON *body* (no auth headers at all for this step) — Mosyle
       responds with a short-lived JWT in the response's `Authorization`
       header (not the body).
    2. Every subsequent POST sends that JWT back as its own `Authorization`
       header, AND still includes "accessToken" in the JSON body.

    Device records already embed the assigned user (userid/username/
    useremail) directly — confirmed via the same reference implementation
    — so no separate /listusers call is needed at all."""

    def __init__(self, base_url: str, access_token: str, admin_email: str, admin_password: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.admin_email = admin_email
        self.admin_password = admin_password
        self._bearer_token: str | None = None

    async def _login(self, client: httpx.AsyncClient) -> str:
        try:
            response = await client.post(
                f"{self.base_url}/login",
                headers={"Content-Type": "application/json"},
                json={
                    "accessToken": self.access_token,
                    "email": self.admin_email,
                    "password": self.admin_password,
                },
            )
        except httpx.HTTPError as exc:
            raise MosyleError(f"Could not reach Mosyle API: {exc}") from exc

        if response.status_code != 200:
            raise MosyleError(
                f"Mosyle login returned HTTP {response.status_code}: {response.text[:300]}"
            )
        bearer = response.headers.get("Authorization")
        if not bearer:
            raise MosyleError(
                "Mosyle login succeeded (HTTP 200) but returned no Authorization header — "
                "check app/integrations/mosyle.py against the real response."
            )
        return bearer

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict) -> dict:
        if self._bearer_token is None:
            self._bearer_token = await self._login(client)

        try:
            response = await client.post(
                f"{self.base_url}/{path.lstrip('/')}",
                headers={"Content-Type": "application/json", "Authorization": self._bearer_token},
                json={**body, "accessToken": self.access_token},
            )
        except httpx.HTTPError as exc:
            raise MosyleError(f"Could not reach Mosyle API: {exc}") from exc

        if response.status_code != 200:
            raise MosyleError(
                f"Mosyle API returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise MosyleError(f"Mosyle API returned non-JSON response: {response.text[:300]}") from exc

        if data.get("status") != "OK":
            raise MosyleError(f"Mosyle API did not return success: {json.dumps(data)[:300]}")
        return data.get("response", data)

    async def list_devices(self, os: str = "mac") -> list[dict]:
        """Paginated — Mosyle returns devices a page at a time plus a
        `rows` total count."""
        devices: list[dict] = []
        page = 0
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            while True:
                result = await self._post(client, "listdevices", {"options": {"os": os, "page": page}})
                page_devices = result.get("devices")
                if page_devices is None:
                    raise MosyleError(
                        "Unexpected response shape from Mosyle /listdevices — expected "
                        "response.devices. Check app/integrations/mosyle.py against the "
                        "real API response."
                    )
                devices.extend(page_devices)
                total = int(result.get("rows", len(devices)))
                if len(devices) >= total or not page_devices or page >= MAX_PAGES:
                    break
                page += 1
        return devices


async def _get_settings_row(db: AsyncSession) -> MosyleSettings | None:
    result = await db.execute(select(MosyleSettings).limit(1))
    return result.scalar_one_or_none()


def _client_from_settings(settings: MosyleSettings) -> MosyleClient:
    if not settings.access_token_encrypted or not settings.admin_email or not settings.admin_password_encrypted:
        raise MosyleError("Mosyle access token / admin credentials are not fully configured.")
    return MosyleClient(
        base_url=settings.base_url,
        access_token=decrypt(settings.access_token_encrypted),
        admin_email=settings.admin_email,
        admin_password=decrypt(settings.admin_password_encrypted),
    )


async def sync_devices(db: AsyncSession) -> int:
    """Refreshes the local MosyleDevice cache from a live API call and
    updates MosyleSettings' sync bookkeeping on success. Raises MosyleError
    on failure without touching last_sync_error itself — see run_sync()."""
    settings = await _get_settings_row(db)
    if settings is None or not settings.enabled:
        raise MosyleError("Mosyle integration is not configured/enabled.")

    client = _client_from_settings(settings)
    devices = await client.list_devices()

    now = datetime.now(UTC)
    await db.execute(delete(MosyleDevice))
    count = 0
    for device in devices:
        mac = device.get("wifi_mac_address") or device.get("bluetooth_mac_address")
        if not mac:
            continue
        db.add(
            MosyleDevice(
                mac_address=normalize_mac(mac),
                serial_number=device.get("serial_number"),
                device_name=device.get("device_name"),
                user_email=device.get("useremail"),
                user_name=device.get("username"),
                synced_at=now,
            )
        )
        count += 1

    settings.last_synced_at = now
    settings.last_sync_error = None
    settings.device_count = count
    await db.commit()
    return count


async def run_sync(db: AsyncSession) -> int:
    """Wrapper for the background loop and the manual /sync endpoint —
    records failure on MosyleSettings.last_sync_error before re-raising,
    so a bad sync is visible in the UI instead of just failing silently."""
    try:
        return await sync_devices(db)
    except MosyleError as exc:
        settings = await _get_settings_row(db)
        if settings is not None:
            settings.last_sync_error = str(exc)
            await db.commit()
        raise
