from datetime import datetime

from pydantic import BaseModel


class TlsCertificateStatusOut(BaseModel):
    issuer: str
    expires_at: datetime
    days_remaining: int


class ServerSettingsUpdate(BaseModel):
    hostname: str | None = None
    require_encryption: bool | None = None
    advertise_ipps: bool | None = None


class ServerSettingsOut(BaseModel):
    hostname: str
    require_encryption: bool
    advertise_ipps: bool
    sync_error: str | None
    # None means nothing has synced yet — see app/core/tls_status.py.
    certificate: TlsCertificateStatusOut | None
