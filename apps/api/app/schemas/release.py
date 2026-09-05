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
    # The number CUPS gave this job, which is what an admin sees on the Jobs
    # page and what someone at the kiosk can read out over the phone when
    # they want the office to release one job and not the rest. Nullable
    # because it is nullable on Job — a row can exist before CUPS has
    # numbered it — so the kiosk falls back to a short form of `id`.
    cups_job_id: int | None = None
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


class HeldJobsOut(BaseModel):
    """The kiosk's answer to a correct PIN: who it decided you are, and what
    of yours is waiting here.

    Wrapping the list rather than returning a bare array so the kiosk can
    show the name it resolved. That matters because the PIN is a Workspace
    Employee ID typed on a shared screen — showing "Jessica Dobrzenski"
    back is how someone catches a mistyped digit that happened to be
    somebody else's ID before they release that person's documents.

    It does not widen what this endpoint discloses. A caller who reaches
    this response already got the person's held document names, and the
    per-token rate limit (app/routers/release.py) bounds guessing at eight
    attempts in five minutes either way.
    """

    person_name: str | None
    jobs: list[HeldJobOut]


class ReleasePinRequest(BaseModel):
    pin: str
