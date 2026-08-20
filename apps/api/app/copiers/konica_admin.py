"""Konica Minolta bizhub Web Connection admin session.

The device's own contracts, captured against real hardware and written up
in docs/copier-capture-konica.md. Two rules from that capture shape this
module:

1. **One admin session per device.** A second login is refused outright
   with AdminAnotherLoginError — including when a human is simply logged
   into the web UI. So sessions are serialized per device with a lock, held
   for as little time as possible, and always logged out (a leaked session
   locks the panel and every other tool).

2. **HTTP 200 means nothing.** Both the JSON WebAPI and the classic CGI
   return 200 for failures; success lives in the payload
   (`MFP.Result.ResultInfo` = Ack/Nack, or `<Item Code="Ok_*">` vs
   `Err_*`). Anything that trusts the status code will silently record
   failures as successes.
"""

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from app.copiers.device_admin import DeviceAdminCredentials

REQUEST_TIMEOUT_SECONDS = 30

# The device's own admin page reads the account list 100 at a time, and
# rejects an over-large window with GeneralRangeIllegal instead of
# clamping it — so this is the device's limit, not a tuning choice.
PAGE_SIZE = 100
MAX_ACCOUNTS = 1000

# The counter read has its own, smaller window: 51 accounts is accepted and
# 266 is refused with GeneralRangeIllegal, so the exact ceiling sits
# somewhere between. 50 is what the device's own Account Track Counter
# screen asks for, which is the number to trust.
COUNTER_PAGE_SIZE = 50

# A window past the end of the account list comes back ArraySize 0 rather
# than as an error, so an empty page is not proof there is nothing beyond
# it — account numbers can be sparse. Two empty windows in a row ends the
# read; anything more thorough would mean 20 round trips to cover a
# 1000-account range that in practice holds a few hundred.
EMPTY_PAGES_BEFORE_STOP = 2

# The device's per-activity counter lists, mapped to the names used
# everywhere above this module.
COUNTER_LISTS = {
    "TotalCounterList": "total",
    "CopyCounterList": "copy",
    "PrintCounterList": "print",
    "ScanFaxCounterList": "scan_fax",
}

# One lock per device IP. The constraint is the device's, not this
# process's, so the key is the address rather than the MfpDevice row.
_DEVICE_LOCKS: dict[str, asyncio.Lock] = {}


def _device_lock(ip: str) -> asyncio.Lock:
    lock = _DEVICE_LOCKS.get(ip)
    if lock is None:
        lock = asyncio.Lock()
        _DEVICE_LOCKS[ip] = lock
    return lock


class KonicaAdminError(Exception):
    """A device-side failure — unreachable, login refused, or a rejected
    request. Carries a message fit to show an admin."""


class KonicaAdminBusy(KonicaAdminError):
    """The device already has an admin session open, so PrintOps can't get
    one. Usually a person logged into the web UI — worth saying so plainly
    rather than reporting a generic failure."""


@dataclass
class TrackCounters:
    """One account's counters as the device reports them: lifetime totals,
    never a period.

    The device only ever increments these; the sole way one goes down is an
    admin pressing Counter Clear on the Account Track Counter page. So a
    caller reading this twice and subtracting is reading usage — reading it
    once tells you the account's whole history, which is almost never the
    question being asked.

    `lists` is kept as the device's own {list: {Type: Count}} rather than
    flattened into named fields: the Type vocabulary differs by model (a
    mono device reports only Bw/BwLarge where a colour one adds FullColor,
    BiColor and MonoColor), and collapsing that here would silently drop
    whatever this firmware happens to report."""

    track_id: str
    lists: dict[str, dict[str, int]]

    @classmethod
    def from_device(cls, row: dict) -> "TrackCounters":
        lists: dict[str, dict[str, int]] = {}
        for device_key, name in COUNTER_LISTS.items():
            counters = row.get(device_key, {}).get("Counter", [])
            # Single-element arrays arrive as an object, same as TrackList.
            if isinstance(counters, dict):
                counters = [counters]
            parsed: dict[str, int] = {}
            for counter in counters:
                try:
                    parsed[str(counter["Type"])] = int(counter["Count"])
                except (KeyError, TypeError, ValueError):
                    continue
            lists[name] = parsed
        return cls(track_id=str(row.get("TrackID", "")), lists=lists)


@dataclass
class TrackAccount:
    """One Account Track account as the device reports it. The password is
    deliberately absent: `TrackPasswordExist` is a boolean and the code is
    never readable, so a sync can diff presence but never the value."""

    track_id: str
    name: str | None
    has_password: bool
    account_stop: bool

    @classmethod
    def from_device(cls, row: dict) -> "TrackAccount":
        return cls(
            track_id=str(row.get("TrackID", "")),
            name=row.get("TrackInfo"),
            has_password=str(row.get("TrackPasswordExist", "")).lower() == "true",
            account_stop=str(row.get("AccountStop", "Off")) == "On",
        )


_H_TOKEN_RE = re.compile(r'(?:id|name)="h_token"\s+value="([^"]+)"')
_ITEM_CODE_RE = re.compile(r'<Item Code="([^"]+)"')
_ERROR_DESC_RE = re.compile(r"<ErrorDescription>([^<]+)</ErrorDescription>")


class KonicaAdminSession:
    """Use via `async with`. Logs in on enter, always logs out on exit."""

    def __init__(self, ip: str, credentials: DeviceAdminCredentials):
        self.ip = ip
        self._credentials = credentials
        self._client: httpx.AsyncClient | None = None
        self._api_token = ""
        self._html_token = ""
        self._lock = _device_lock(ip)

    async def __aenter__(self) -> "KonicaAdminSession":
        await self._lock.acquire()
        try:
            self._client = httpx.AsyncClient(
                base_url=f"http://{self.ip}/wcd", timeout=REQUEST_TIMEOUT_SECONDS
            )
            await self._login()
        except BaseException:
            await self._close()
            self._lock.release()
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        try:
            await self._logout()
        finally:
            await self._close()
            self._lock.release()

    async def _close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _login(self) -> None:
        client = self._client
        assert client is not None
        try:
            await client.get("/index.html")
            response = await client.post(
                "/login.cgi",
                data={
                    "func": "PSL_LP1_LOG",
                    "R_ADM": "AdminAdmin",
                    # The bizhub admin login has no username concept; the
                    # field is posted empty (see device_admin.py).
                    "username": self._credentials.username,
                    "password": self._credentials.password,
                },
                headers={"Referer": f"http://{self.ip}/wcd/spa_login.html"},
            )
        except httpx.HTTPError as exc:
            raise KonicaAdminError(f"Could not reach the copier at {self.ip}: {exc}") from exc

        if "AdminAnotherLoginError" in response.text:
            raise KonicaAdminBusy(
                "Someone is already logged into this copier's admin page. "
                "Log out of the copier's web interface and try again."
            )

        match = _H_TOKEN_RE.search(response.text)
        self._html_token = match.group(1) if match else ""

        # Never trust the login body alone — confirm with an admin-only read.
        probe = await client.get("/api/AppReqGetCustomData/_A-00-00001")
        if probe.status_code != 200 or len(probe.content) < 2000:
            raise KonicaAdminError(
                "The copier rejected the stored admin password. Check it on the "
                "copier's page in PrintOps — repeated failures lock the device's "
                "admin account for 5 minutes."
            )

    async def _logout(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.post("/a_user.cgi", data={"func": "PSL_ACO_LGO"})
        except httpx.HTTPError:
            # Logout is best-effort; the device times sessions out on its
            # own. Never mask the original error with a logout failure.
            pass

    async def webapi(self, endpoint: str, payload: dict | None = None) -> dict:
        """POST the JSON WebAPI. Raises on Nack — callers that expect a Nack
        (a capability probe, say) should catch it."""
        client = self._client
        assert client is not None
        body = dict(payload or {})
        body["Token"] = self._api_token
        response = await client.post(
            f"/api/{endpoint}",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise KonicaAdminError(f"{endpoint} returned a non-JSON response.") from exc

        mfp = data.get("MFP", {})
        if mfp.get("Token"):
            self._api_token = mfp["Token"]
        result = mfp.get("Result", {})
        if result.get("ResultInfo") != "Ack":
            detail = result.get("ErrorDetails", "unknown error")
            field = result.get("ErrorDescription")
            raise KonicaAdminError(f"{endpoint} failed: {detail}{f' ({field})' if field else ''}")
        return mfp

    async def cgi(self, payload: dict) -> str:
        """POST the classic admin CGI, echoing the rotating h_token, and
        raise unless the reply carries an Ok_* item code. HTTP 200 is
        returned for failures too, so the code is the only real signal."""
        client = self._client
        assert client is not None
        body = dict(payload)
        body["h_token"] = self._html_token
        response = await client.post("/a_user.cgi", data=body)

        token_match = _H_TOKEN_RE.search(response.text)
        if token_match:
            self._html_token = token_match.group(1)

        code_match = _ITEM_CODE_RE.search(response.text)
        code = code_match.group(1) if code_match else ""
        if not code.startswith("Ok"):
            field = _ERROR_DESC_RE.search(response.text)
            detail = f" ({field.group(1)})" if field else ""
            raise KonicaAdminError(f"{code or 'unrecognised response'}{detail}")
        return response.text

    async def list_accounts(self, limit: int = MAX_ACCOUNTS) -> list[TrackAccount]:
        """Current Account Track accounts. Returns [] when the feature is
        enabled but empty; raises KonicaAdminError with AuthNotTrackMode
        when Account Track is switched off entirely.

        Paged, because the device rejects an over-large window outright
        with GeneralRangeIllegal rather than clamping it — asking for all
        1000 in one call fails even when there are three accounts."""
        accounts: list[TrackAccount] = []
        start = 1  # 1-based; Start: 0 is rejected as GeneralRangeIllegal.
        while start <= limit:
            length = min(PAGE_SIZE, limit - start + 1)
            mfp = await self.webapi(
                "AppReqGetTrackSetting",
                {
                    "TrackListCondition": {
                        "TrackType": "Private",
                        "ObtainCondition": {
                            "Type": "OffsetList",
                            "OffsetRange": {"Start": start, "Length": length},
                        },
                    }
                },
            )
            track_list = mfp.get("TrackList", {})
            rows = track_list.get("Track", [])
            # The device collapses a single-element array into an object.
            if isinstance(rows, dict):
                rows = [rows]
            accounts.extend(TrackAccount.from_device(row) for row in rows)
            if len(rows) < length:
                break
            start += length
        return accounts

    async def list_account_counters(self, limit: int = MAX_ACCOUNTS) -> list[TrackCounters]:
        """Every account's lifetime counters.

        `AppReqGetTrackCounterInfo` — read out of the Account Track Counter
        screen's own JS (spa_003_002_TCR001.tmpl.html), which is why an
        earlier capture concluded per-account counters weren't on the
        WebAPI at all: the endpoint that returns Nack/"Webapi not
        supported" is `AppReqGetTrackCounter`, one word short of the real
        one.

        Windows are ID ranges, not offsets — Start/End are account numbers,
        and only the accounts that exist within the range come back."""
        counters: list[TrackCounters] = []
        start = 1
        empty_pages = 0
        while start <= limit and empty_pages < EMPTY_PAGES_BEFORE_STOP:
            end = min(start + COUNTER_PAGE_SIZE - 1, limit)
            mfp = await self.webapi(
                "AppReqGetTrackCounterInfo",
                {
                    "TrackCounterListCondition": {
                        "TrackType": "Private",
                        "ObtainCondition": {
                            "Type": "IndexList",
                            "IndexRange": {"Start": start, "End": end},
                        },
                        "BackUp": "false",
                    }
                },
            )
            rows = mfp.get("TrackCounterList", {}).get("TrackCounter", [])
            if isinstance(rows, dict):
                rows = [rows]
            empty_pages = empty_pages + 1 if not rows else 0
            counters.extend(TrackCounters.from_device(row) for row in rows)
            start = end + 1
        return counters

    async def write_account(
        self,
        track_number: str,
        name: str,
        password: str,
        label: str | None = None,
        registration_max: int = MAX_ACCOUNTS,
        replace_existing: bool = False,
    ) -> None:
        """Register (or, with replace_existing, edit) one Account Track
        account.

        Three field-length facts, all verified against hardware:
        AA_TRA_T_NAM is capped at 8 characters, AA_TRA_T_INF at 20, and the
        password at 64 — the opposite of the usual assumption that the
        8-character cap applies to the password, and the reason staff IDs
        go in the password.

        `label` (AA_TRA_T_INF) is the only one of the three that reads back
        through the API, as TrackInfo, and it is what an admin sees when
        looking at the copier's own account list. Without it every account
        shows blank and there is no way to tell whose is whose.

        The device refuses a password already in use ("Don't set Duplicate
        Password"), so re-registering an existing person fails — which is
        why callers must skip accounts already provisioned rather than
        relying on the device to no-op."""
        await self.cgi(
            {
                "func": "PSL_AA_TRA_TRA",
                # "new" registers; an existing number edits in place, which
                # is how an account gains a label without being deleted
                # (the delete contract is not captured).
                "AA_TRA_H_NUM": str(track_number) if replace_existing else "new",
                "trackType": "Password",
                "AA_TRA_R_RNM": "Direct",
                "AA_TRA_T_MAX": str(registration_max),
                "AA_TRA_T_NUM": str(track_number),
                "AA_TRA_T_NAM": name[:8],
                "AA_TRA_T_INF": (label or name)[:20],
                "AA_TRA_P_UP": password,
                "AA_TRA_P_CMP": password,
            }
        )

    # Kept for callers that only ever create.
    async def create_account(
        self, track_number: str, name: str, password: str, registration_max: int = MAX_ACCOUNTS
    ) -> None:
        await self.write_account(track_number, name, password, registration_max=registration_max)
