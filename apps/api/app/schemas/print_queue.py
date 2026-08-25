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
