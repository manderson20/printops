from typing import Literal
from uuid import UUID

from pydantic import BaseModel

QuotaPeriod = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


class QuotaSettingsOut(BaseModel):
    enabled: bool


class QuotaSettingsUpdate(BaseModel):
    enabled: bool | None = None


class PrinterUserQuotaCreate(BaseModel):
    # None = default/wildcard row for this printer (see PrinterUserQuota's
    # docstring, app/models/quota.py).
    user_email: str | None = None
    period: QuotaPeriod
    page_limit: int


class PrinterUserQuotaUpdate(BaseModel):
    period: QuotaPeriod | None = None
    page_limit: int | None = None


class PrinterUserQuotaOut(BaseModel):
    id: UUID
    printer_id: UUID
    user_email: str | None
    period: QuotaPeriod
    page_limit: int
    # This period's usage so far, computed at read time (never stored) —
    # see app/quotas/service.py:get_pages_used/period_bounds.
    pages_used: int

    model_config = {"from_attributes": True}
