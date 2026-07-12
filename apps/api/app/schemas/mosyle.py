from datetime import datetime

from pydantic import BaseModel, field_validator

from app.core.validation import validate_base_url


class MosyleSettingsUpdate(BaseModel):
    base_url: str | None = None
    access_token: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    enabled: bool | None = None

    _validate_base_url = field_validator("base_url")(validate_base_url)


class MosyleSettingsOut(BaseModel):
    """Never returns decrypted secrets — booleans indicate whether they're
    set, matching the masking pattern used elsewhere (e.g.
    PrinterConnectionOut doesn't expose the backend token)."""

    base_url: str
    admin_email: str | None
    has_access_token: bool
    has_admin_password: bool
    enabled: bool
    last_synced_at: datetime | None
    last_sync_error: str | None
    device_count: int


class MosyleTestResult(BaseModel):
    ok: bool
    device_count: int | None = None
    error: str | None = None
