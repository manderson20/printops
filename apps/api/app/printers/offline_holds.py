"""Releasing the jobs that were held because their printer was unreachable.

CUPS queues behind an absent printer on its own, but only for MaxJobTime — the
default is three hours, after which cupsd cancels the job outright. That is the
right behaviour for a printer somebody is about to switch on, and destructive
for one that is off overnight: on 2026-08-23 a teacher's job sent at 5pm to a
classroom printer was due to be destroyed at 8pm, with no notice to her and
nothing left in the queue by morning.

So a job for a printer PrintOps knows is offline is held instead
(app/quotas/service.py:resolve_hold_reason). It waits as long as it needs to,
it is visible on the Held Jobs page while it waits, and this is what lets it go
when the printer answers again.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError, submit_released_job

logger = logging.getLogger(__name__)

HOLD_REASON = "printer_offline"


async def release_jobs_waiting_for(db: AsyncSession, printer: Printer) -> int:
    """Sends everything that was waiting for this printer, oldest first.

    Returns how many were handed to CUPS. Does not commit — the caller owns
    the transaction, matching app/printers/status.py's convention.

    Oldest first because that is the order the people who sent them pressed
    print, and a queue that reorders itself under load is a queue nobody can
    predict. A job whose delivery fails is recorded as failed with the reason
    rather than left held: it has had its turn, and a held job that silently
    retries on every reconnection is one nobody can see failing.
    """
    waiting = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.printer_id == printer.id,
                    Job.status == "held",
                    Job.hold_reason == HOLD_REASON,
                )
                .order_by(Job.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not waiting:
        return 0

    released = 0
    for job in waiting:
        if not job.held_file_path:
            # Nothing to send: the spool step never completed, so there is no
            # document to deliver. Closing it is honest; leaving it held would
            # promise a print that can never happen.
            job.status = "failed"
            job.error_message = "Held while the printer was offline, but the document was lost."
            job.completed_at = datetime.now(UTC)
            continue
        try:
            await asyncio.to_thread(
                submit_released_job,
                str(printer.id),
                job.held_file_path,
                job.document_name,
                job.copy_count,
                job.held_job_options,
            )
        except ReleaseError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.now(UTC)
            logger.warning(
                "Could not release %s's held job for %s: %s", job.submitted_by, printer.name, exc
            )
            continue

        job.status = "forwarded"
        job.completed_at = datetime.now(UTC)
        Path(job.held_file_path).unlink(missing_ok=True)
        job.held_file_path = None
        released += 1

    if released:
        logger.warning(
            "%s came back: released %d job(s) that were waiting for it.", printer.name, released
        )
    return released
