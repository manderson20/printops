from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Severity = Literal["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


class SyslogSettingsOut(BaseModel):
    enabled: bool
    port: int
    min_severity: Severity
    retention_days: int


class SyslogSettingsUpdate(BaseModel):
    enabled: bool | None = None
    port: int | None = None
    min_severity: Severity | None = None
    retention_days: int | None = None


class SyslogEventOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    printer_id: UUID | None
    source_ip: str
    received_at: datetime
    device_timestamp: datetime | None
    severity: Severity | None
    facility: int | None
    hostname: str | None
    app_name: str | None
    message: str
    raw: str


class SyslogEventListOut(SyslogEventOut):
    """SyslogEventOut plus the printer's display name — joined in by the
    list endpoints (app/routers/printers.py, app/routers/syslog.py) the
    same way JobListOut adds printer_name onto JobOut."""

    printer_name: str | None


class SyslogEventPage(BaseModel):
    items: list[SyslogEventListOut]
    total: int
    page: int
    page_size: int


class SyslogEventIngest(BaseModel):
    """One parsed syslog message, as posted by infra/syslog-relay/server.py
    to POST /api/v1/internal/syslog/events. source_ip (not printer_id) is
    what the relay knows — matching to a Printer happens server-side in
    app/syslog/service.py:ingest_events, so a printer added/re-IP'd after
    the relay's own ip-map cache last refreshed still gets matched
    correctly on the next poll's worth of settings, not just the relay's
    stale local snapshot."""

    source_ip: str
    received_at: datetime
    device_timestamp: datetime | None = None
    severity: Severity | None = None
    facility: int | None = None
    hostname: str | None = None
    app_name: str | None = None
    message: str
    raw: str


class SyslogIngestRequest(BaseModel):
    events: list[SyslogEventIngest]


class SyslogIngestResult(BaseModel):
    accepted: int
    dropped: int
