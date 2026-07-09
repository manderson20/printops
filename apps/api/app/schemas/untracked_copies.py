from datetime import datetime

from pydantic import BaseModel


class UntrackedCopySettingsOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None


class UntrackedCopySettingsUpdate(BaseModel):
    enabled: bool | None = None


class UntrackedCopySummaryOut(BaseModel):
    measured_copies: int
    estimated_untracked: int
    tracking_since: datetime | None
