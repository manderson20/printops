from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.audit import MIN_AUDIT_RETENTION_DAYS


class AuditSettingsOut(BaseModel):
    retention_days: int

    model_config = {"from_attributes": True}


class AuditSettingsUpdate(BaseModel):
    # Floored, not merely defaulted. An audit trail an admin can shorten to a
    # day is weaker evidence than one they cannot, and the obvious way to bury
    # an action is to set retention below the age of the thing you want gone.
    # The ceiling is arbitrary but stops a typo turning into an unbounded table.
    retention_days: int = Field(ge=MIN_AUDIT_RETENTION_DAYS, le=3650)


class AuditEventOut(BaseModel):
    id: UUID
    occurred_at: datetime
    actor_email: str
    actor_role: str
    impersonated_by: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    entity_label: str | None
    summary: str
    changes: dict[str, Any] | None
    source_ip: str | None

    model_config = {"from_attributes": True}


class AuditEventPage(BaseModel):
    events: list[AuditEventOut]
    total: int
    page: int
    page_size: int


class AuditActionsOut(BaseModel):
    """The distinct action strings present in the log, for the UI's filter.

    Read from the data rather than from a hardcoded list so a newly instrumented
    endpoint appears in the filter without anyone remembering to add it — and so
    the filter never offers an action that has never happened here.
    """

    actions: list[str]
