import base64
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models.mosyle import MosyleDevice, MosyleSettings

REQUEST_TIMEOUT_SECONDS = 30


class MosyleError(Exception):
    pass


def normalize_mac(mac: str) -> str:
    return mac.strip().upper().replace("-", ":")


class MosyleClient:
    """Thin wrapper around Mosyle's Business API. Field/endpoint names here
    are triangulated from third-party integration scripts, not Mosyle's own
    docs (paywalled behind an active account) — if Mosyle's real response
    doesn't match, this is the file to fix; nothing else should need to
    change. See ARCHITECTURE.md's Mosyle section for the research trail."""

    def __init__(self, base_url: str, access_token: str, admin_email: str, admin_password: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.admin_email = admin_email
        self.admin_password = admin_password

    def _headers(self) -> dict[str, str]:
        basic = base64.b64encode(f"{self.admin_email}:{self.admin_password}".encode()).decode()
        return {
            "accesstoken": self.access_token,
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict) -> dict | list:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(
                    f"{self.base_url}{path}", headers=self._headers(), json=body
                )
            except httpx.HTTPError as exc:
                raise MosyleError(f"Could not reach Mosyle API: {exc}") from exc

        if response.status_code != 200:
            raise MosyleError(
                f"Mosyle API returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise MosyleError(f"Mosyle API returned non-JSON response: {response.text[:300]}") from exc

    async def list_devices(self, os: str = "mac") -> list[dict]:
        body = await self._post(
            "/listdevices", {"operation": "list", "options": {"os": os, "dataColumns": ["*"]}}
        )
        try:
            return body[0]["devices"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise MosyleError(
                "Unexpected response shape from Mosyle /listdevices — expected "
                "response[0].devices. Check app/integrations/mosyle.py against the "
                "real API response."
            ) from exc

    async def list_users(self) -> dict[str, dict]:
        """Returns {userid: {"email": ..., "name": ...}}."""
        body = await self._post("/listusers", {"operation": "list"})
        try:
            users = body[0]["users"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise MosyleError(
                "Unexpected response shape from Mosyle /listusers — expected "
                "response[0].users. Check app/integrations/mosyle.py against the "
                "real API response."
            ) from exc
        return {
            str(user["id"]): {"email": user.get("email"), "name": user.get("name")}
            for user in users
            if user.get("id") is not None
        }


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
    users = await client.list_users()

    now = datetime.now(UTC)
    await db.execute(delete(MosyleDevice))
    count = 0
    for device in devices:
        mac = device.get("wifi_mac_address") or device.get("ethernet_mac_address")
        if not mac:
            continue
        user = users.get(str(device.get("userid")), {})
        db.add(
            MosyleDevice(
                mac_address=normalize_mac(mac),
                serial_number=device.get("serial_number"),
                device_name=device.get("device_name"),
                user_email=user.get("email"),
                user_name=user.get("name"),
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
