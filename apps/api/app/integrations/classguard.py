import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.models.classguard import ClassGuardSettings

REQUEST_TIMEOUT_SECONDS = 5  # in the hot path of resolving a print job's user — fail fast


class ClassGuardError(Exception):
    pass


class ClassGuardClient:
    """Client for this org's own ClassGuard platform (DHCP/DNS/web
    filter) — used purely for its DHCP lease table, to resolve a print
    job's source IP to a MAC address that can then be matched against
    Mosyle's cached device list (app/attribution/resolve.py). Contract:
    GET {base_url}/api/v1/lookup?ip=<ip>, header X-ClassGuard-Token,
    200 {"mac_address": "..."} on a hit, 404 on no active lease (expected/
    normal, not an error)."""

    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    async def lookup_mac(self, ip: str) -> str | None:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/api/v1/lookup",
                    params={"ip": ip},
                    headers={"X-ClassGuard-Token": self.access_token},
                )
            except httpx.HTTPError as exc:
                raise ClassGuardError(f"Could not reach ClassGuard: {exc}") from exc

        if response.status_code == 404:
            return None  # no active lease for this IP — normal, not an error
        if response.status_code != 200:
            raise ClassGuardError(
                f"ClassGuard returned HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ClassGuardError(f"ClassGuard returned non-JSON response: {response.text[:300]}") from exc

        mac = data.get("mac_address")
        if not mac:
            raise ClassGuardError(
                "Unexpected response shape from ClassGuard — expected mac_address. "
                "Check app/integrations/classguard.py against the real API response."
            )
        return mac


async def get_settings(db: AsyncSession) -> ClassGuardSettings | None:
    result = await db.execute(select(ClassGuardSettings).limit(1))
    return result.scalar_one_or_none()


def client_from_settings(settings: ClassGuardSettings) -> ClassGuardClient:
    if not settings.access_token_encrypted:
        raise ClassGuardError("ClassGuard access token is not configured.")
    return ClassGuardClient(
        base_url=settings.base_url,
        access_token=decrypt(settings.access_token_encrypted),
    )
