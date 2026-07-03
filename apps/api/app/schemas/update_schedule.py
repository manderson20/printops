from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class VersionOut(BaseModel):
    version: str


class UpdateCheckOut(BaseModel):
    current_version: str
    latest_version: str
    update_available: bool
    changelog: str | None


class ScheduleUpdateIn(BaseModel):
    scheduled_at: datetime
    target_version: str


class UpdateScheduleOut(BaseModel):
    id: UUID
    target_version: str
    scheduled_at: datetime
    status: str
    log: str | None
    requested_by: str | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateStatusOut(BaseModel):
    pending: UpdateScheduleOut | None


class UpdateCompleteIn(BaseModel):
    status: str
    log: str | None = None
