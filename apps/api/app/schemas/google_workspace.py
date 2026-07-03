from datetime import datetime

from pydantic import BaseModel


class GoogleWorkspaceSettingsUpdate(BaseModel):
    service_account_json: str | None = None
    admin_email: str | None = None
    customer_id: str | None = None
    enabled: bool | None = None


class GoogleWorkspaceSettingsOut(BaseModel):
    """Never returns the decrypted service account key — has_service_account_json
    indicates whether it's set, matching the Mosyle/ClassGuard masking pattern."""

    admin_email: str | None
    customer_id: str
    has_service_account_json: bool
    enabled: bool
    last_synced_at: datetime | None
    last_sync_error: str | None
    device_count: int


class GoogleWorkspaceTestResult(BaseModel):
    ok: bool
    device_count: int | None = None
    error: str | None = None


class GoogleWorkspaceUserOut(BaseModel):
    email: str
    name: str | None
