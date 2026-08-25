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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError, submit_released_job

logger = logging.getLogger(__name__)

HOLD_REASON = "printer_offline"

# The keyword the UI keys off to say this printer needs looking at, rather than
# simply being away.
UNREACHABLE_REASON = "printops-unreachable-with-jobs"

# How long a printer that used to work may be away with jobs waiting before
# that is worth saying out loud. A day, so a printer switched off overnight or
# over a weekend does not raise anything: those are ordinary, the jobs are safe,
# and an alarm that fires every Saturday is one nobody reads by October.
AWAY_TOO_LONG = timedelta(hours=24)


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
                # See the release queue's own note in app/printers/release.py:
                # nothing else strips the page size on this path.
                bool((printer.capabilities or {}).get("media_col_broken")),
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


async def waiting_alert(
    db: AsyncSession, printer: Printer, now: datetime | None = None
) -> str | None:
    """Whether this offline printer's waiting jobs are worth an admin's
    attention, and what to tell them.

    Jobs waiting for a printer that is off overnight are fine — they go when it
    is switched on, and saying anything would be noise. Two cases are not fine,
    and both look identical to "switched off" from the outside:

    A printer that has *never* been reachable is almost always a wrong address
    rather than a dark room. That is what happened to ES 4th Grade Printer on
    2026-08-23: the record said 10.20.3.104, the printer was at 10.10.1.10, and
    a teacher's job waited a day for somewhere nothing was ever going to answer.
    Reported as soon as one job is waiting, because there is nothing to wait
    for.

    A printer that used to work and has been away more than a day with work
    piling up behind it might be broken, moved, or decommissioned — and either
    way somebody should decide, rather than the queue quietly growing.
    """
    now = now or datetime.now(UTC)
    waiting = (
        await db.execute(
            select(func.count(Job.id), func.min(Job.created_at)).where(
                Job.printer_id == printer.id,
                Job.status == "held",
                Job.hold_reason == HOLD_REASON,
            )
        )
    ).one()
    count, oldest = waiting
    if not count:
        return None

    jobs = "job" if count == 1 else "jobs"
    waited = _describe_wait(oldest, now)

    if printer.last_online_at is None:
        return (
            f"{count} {jobs} waiting, and this printer has never answered at "
            f"{printer.ip_address}. That is usually the wrong address rather than a printer "
            f"that is switched off — nothing will print until it is corrected. Oldest has "
            f"been waiting {waited}."
        )

    away = now - printer.last_online_at
    if away < AWAY_TOO_LONG:
        return None
    gone_for = _describe_wait(printer.last_online_at, now)
    return (
        f"{count} {jobs} waiting, and this printer has not answered for {gone_for}. They will "
        f"print when it comes back, but a printer away this long with work behind it is usually "
        f"switched off for good, moved, or on a changed address. Oldest job has been waiting "
        f"{waited}."
    )


def _describe_wait(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "an unknown time"
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    hours = int((now - since).total_seconds() // 3600)
    if hours < 1:
        return "less than an hour"
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"
