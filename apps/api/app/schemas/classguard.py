from datetime import datetime

from pydantic import BaseModel


class ClassGuardSettingsUpdate(BaseModel):
    base_url: str | None = None
    access_token: str | None = None
    enabled: bool | None = None


class ClassGuardTestRequest(ClassGuardSettingsUpdate):
    # ClassGuard's only contract is "look up this IP" — there's no
    # separate health/ping endpoint, so testing requires a real IP to
    # look up. Use any client currently on the network.
    test_ip: str


class ClassGuardSettingsOut(BaseModel):
    """Never returns the decrypted token — has_access_token indicates
    whether it's set, matching the Mosyle settings masking pattern."""

    base_url: str
    has_access_token: bool
    enabled: bool
    last_test_at: datetime | None
    last_test_error: str | None


class ClassGuardTestResult(BaseModel):
    ok: bool
    mac_address: str | None = None
    error: str | None = None
