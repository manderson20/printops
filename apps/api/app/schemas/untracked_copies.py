from datetime import datetime

from pydantic import BaseModel


class UntrackedCopySettingsOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None


class UntrackedCopySettingsUpdate(BaseModel):
    enabled: bool | None = None


class UntrackedCopyPrinterEntryOut(BaseModel):
    printer_id: str
    printer_name: str
    measured_copies: int
    estimated_untracked: int


class UntrackedCopySummaryOut(BaseModel):
    measured_copies: int
    estimated_untracked: int
    tracking_since: datetime | None
    printers: list[UntrackedCopyPrinterEntryOut]
