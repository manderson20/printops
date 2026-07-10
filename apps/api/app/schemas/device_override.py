from datetime import datetime

from pydantic import BaseModel


class KnownDeviceOut(BaseModel):
    """A device seen in either MDM cache (Mosyle or Google Workspace),
    merged with whatever attribution override is currently set for it —
    the admin-facing view used to disambiguate/correct attribution."""

    mac_address: str
    source: str  # "mosyle" | "google_workspace"
    serial_number: str | None
    device_name: str | None
    reported_email: str | None
    reported_username: str | None = None
    override_email: str | None
    override_note: str | None


class KnownDevicePage(BaseModel):
    items: list[KnownDeviceOut]
    total: int
    page: int
    page_size: int


class DeviceOverrideUpdate(BaseModel):
    resolved_email: str
    note: str | None = None


class DeviceOverrideOut(BaseModel):
    mac_address: str
    resolved_email: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    backfilled_job_count: int
