from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PrintReleaseSettingsOut(BaseModel):
    hold_expiry_hours: float


class PrintReleaseSettingsUpdate(BaseModel):
    hold_expiry_hours: float | None = None


class HeldJobOut(BaseModel):
    """What the public kiosk (app/routers/release.py) shows for one of the
    resolved person's held jobs at that printer — deliberately minimal,
    no internal ids/attribution details beyond what the kiosk UI needs,
    since this is an unauthenticated (PIN-gated, not JWT-gated) surface."""

    id: UUID
    status: str
    document_name: str | None
    page_count: int | None
    created_at: datetime
    held_expires_at: datetime | None
    # The originating printer's name, only when it differs from the kiosk
    # being viewed (i.e. a follow_me job released somewhere other than
    # where it was submitted) — None for an ordinary same-printer
    # pin_release job, so the kiosk UI only shows it when it'd disambiguate
    # anything. Attached by the router (app/routers/release.py), not a real
    # Job column, so from_attributes alone won't populate it.
    printer_name: str | None = None

    model_config = {"from_attributes": True}


class ReleasePinRequest(BaseModel):
    pin: str
