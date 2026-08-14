from typing import Literal
from uuid import UUID

from pydantic import BaseModel

QuotaPeriod = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]
# See Printer.quota_mode (app/models/printer.py) for what each mode means.
QuotaMode = Literal["include", "exclude"]


class QuotaSettingsOut(BaseModel):
    enabled: bool


class QuotaSettingsUpdate(BaseModel):
    enabled: bool | None = None


class PrinterUserQuotaCreate(BaseModel):
    # None = default/wildcard row for this printer (see PrinterUserQuota's
    # docstring, app/models/quota.py).
    user_email: str | None = None
    period: QuotaPeriod
    # None = an exclude-mode exemption: this user is let out of the printer's
    # blanket limit rather than given a number of their own. Rejected for a
    # blanket row, and on an include-mode printer, by create_printer_quota —
    # neither has anything to be exempt from.
    page_limit: int | None = None


class PrinterUserQuotaUpdate(BaseModel):
    period: QuotaPeriod | None = None
    page_limit: int | None = None


class PrinterUserQuotaOut(BaseModel):
    id: UUID
    printer_id: UUID
    user_email: str | None
    period: QuotaPeriod
    page_limit: int | None
    # This period's usage so far, computed at read time (never stored) —
    # see app/quotas/service.py:get_pages_used/period_bounds.
    pages_used: int
    # Whether this row is what actually governs the user right now, given the
    # printer's current quota_mode — so the UI can flag rows that are being
    # ignored (a blanket row on an include-mode printer, say) instead of
    # showing a limit that isn't being enforced.
    active: bool

    model_config = {"from_attributes": True}
