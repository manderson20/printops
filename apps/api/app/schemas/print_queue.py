from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class PrintQueueJobOut(BaseModel):
    """One job in a printer's line, as one person is allowed to see it.

    Someone else's job is deliberately reduced to its size and its place in
    the queue: enough to answer "is anyone actually waiting behind me?", which
    is the question the yield button exists to serve, without putting every
    document title in the building in front of every member of staff. The
    redaction happens server-side (app/routers/print_queue.py) — `document_name`
    is null for other people's jobs rather than hidden by the browser."""

    cups_job_id: int
    # 1-based place in this printer's line, in the order cupsd will print
    # them — not a job id, and not stable across refreshes by design.
    position: int
    mine: bool
    document_name: str | None = None
    size_bytes: int | None = None
    # "printing" is the job currently going to the device; "waiting" is
    # everything queued behind it; "held" is a job PrintOps or an admin has
    # deliberately parked (over quota, waiting for release at the printer).
    state: Literal["printing", "waiting", "held"]
    # True when this job has already been moved to the back of the line.
    yielded: bool
    can_yield: bool
    can_restore: bool
    submitted_at: datetime | None = None


class PrintQueuePrinterOut(BaseModel):
    printer_id: UUID
    printer_name: str
    jobs: list[PrintQueueJobOut]
    # How many of the jobs above are this person's, so the page can lead with
    # "your job is 3rd of 5" without the browser recounting.
    my_job_count: int
    total_job_count: int


class PrintQueueHeldJobOut(BaseModel):
    """A job PrintOps is holding rather than one cupsd has queued.

    These are the jobs that used to be invisible to the person who sent them:
    they never enter a printer's queue at all, so nothing on the queue side of
    this page would ever show them, and their owner had no way to tell a job
    that was waiting from one that had vanished. Only ever this person's own —
    a held job occupies nobody else's place in any line, so there is no
    "should I yield?" question it helps anyone answer, and listing other
    people's would be exposure without purpose."""

    job_id: UUID
    printer_id: UUID
    printer_name: str
    document_name: str | None = None
    size_bytes: int | None = None
    # Why PrintOps is holding it — see app/quotas/service.py:resolve_hold_reason,
    # which is the single place that decides. None only for a legacy row held
    # before the reason was recorded.
    reason: Literal["pin_release", "follow_me", "quota", "printer_offline"] | None = None
    submitted_at: datetime
    # When the hold is swept and the document deleted unprinted, where a
    # deadline applies (PrintReleaseSettings.hold_expiry_hours).
    expires_at: datetime | None = None


class PrintQueueOut(BaseModel):
    """Both halves of "what is happening to my printing right now".

    They are genuinely different things and the page keeps them apart: a queued
    job is in a line behind other people, while a held job is waiting on a
    person, a quota, or a printer being switched back on."""

    queues: list[PrintQueuePrinterOut]
    held: list[PrintQueueHeldJobOut]
    # True when cupsd could not be asked what is queued. The two halves come
    # from different places — the queue from cupsd, the holds from PrintOps'
    # own database — so one being unavailable must not take the other down
    # with it. A print server having trouble is precisely when someone wants
    # to know their held job is still safe.
    queue_unavailable: bool = False
