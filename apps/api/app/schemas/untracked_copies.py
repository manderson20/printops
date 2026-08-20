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


class TrackedCopyDeviceEntryOut(BaseModel):
    device_id: str
    device_name: str
    building: str | None
    copy_pages: int
    scan_pages: int
    fax_pages: int
    people: int
    unattributed_pages: int


class TrackedCopySummaryOut(BaseModel):
    """The named counterpart to UntrackedCopySummaryOut. Shown beside it so
    the pair reads as coverage rather than as a total on its own."""

    copy_pages: int
    scan_pages: int
    fax_pages: int
    people: int
    unattributed_pages: int
    devices_reporting: int
    devices: list[TrackedCopyDeviceEntryOut]
