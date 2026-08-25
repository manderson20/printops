"""Throwing a held job away, for whoever is entitled to do it.

Two surfaces now reach this: an admin clearing any hold
(app/routers/held_jobs.py) and a person clearing one of their own from the
queue page (app/routers/print_queue.py). One implementation rather than two,
because the careful part is not the policy — it is the claim, and a second
copy of it would be a second chance to get that wrong.

**Why the claim is a conditional UPDATE rather than read-then-write.** This
races the automatic release of printer_offline holds: the status loop
(app/printers/offline_holds.py) submits a held document the moment its printer
answers, in another session. Both could see status == "held" and act — one
printing the document while the history says it was discarded, or the release
failing on a file this already deleted. Whoever moves the row out of "held"
first wins; the loser is told so and touches nothing.

**Ownership is part of the same claim**, not a check before it. A separate
read would leave a window in which the row changed hands (an admin override
re-attributing a job, say) between the check and the write.

**The document is destroyed; the record is not.** Disposal matches the expiry
sweep in app/main.py exactly, so a discarded hold means one thing rather than
two: the spool file goes, the row stays and is marked cancelled. The job
remains in history and in the reports. That asymmetry is the whole point, and
it is why this is not a DELETE.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job


class HoldDiscardError(Exception):
    """Base for the reasons a discard didn't happen."""


class HoldNotFound(HoldDiscardError):
    """No such held job — or none this caller is allowed to see.

    Deliberately the same answer for both: a caller restricted to their own
    jobs must not be able to learn that somebody else's job exists by
    discarding at its id and reading the error."""


class HoldNoLongerHeld(HoldDiscardError):
    def __init__(self, current_status: str) -> None:
        self.current_status = current_status
        super().__init__(f"This job is no longer held — it is {current_status}.")


async def discard_hold(
    db: AsyncSession,
    job_id: UUID,
    *,
    owned_by: list[str] | None = None,
) -> Job:
    """Discards one held job and returns the resulting row.

    `owned_by` restricts the discard to jobs submitted by one of the given
    identities (case-insensitively). None means unrestricted, which is the
    admin path.
    """
    now = datetime.now(UTC)
    # Read before the claim: RETURNING hands back the *new* row, and the claim
    # below nulls this column, so asking for it there returns None and the
    # document is never deleted. Only these paths ever write it.
    existing = await db.get(Job, job_id)
    spool_path = existing.held_file_path if existing else None

    conditions = [Job.id == job_id, Job.status == "held"]
    if owned_by is not None:
        conditions.append(func.lower(Job.submitted_by).in_([o.casefold() for o in owned_by]))

    claimed = await db.execute(
        update(Job)
        .where(*conditions)
        .values(
            status="cancelled",
            error_message="Discarded without printing",
            completed_at=now,
            held_file_path=None,
        )
        .returning(Job.id)
    )
    if claimed.first() is None:
        await db.rollback()
        job = await db.get(Job, job_id)
        if job is None:
            raise HoldNotFound
        if owned_by is not None and not _submitted_by_one_of(job, owned_by):
            raise HoldNotFound
        raise HoldNoLongerHeld(job.status)

    # The file is removed only after the claim is committed. Deleting first
    # would destroy the document of a job this turned out not to own.
    await db.commit()
    if spool_path:
        Path(spool_path).unlink(missing_ok=True)

    job = await db.get(Job, job_id)
    await db.refresh(job)
    return job


def _submitted_by_one_of(job: Job, identities: list[str]) -> bool:
    if not job.submitted_by:
        return False
    submitted = job.submitted_by.casefold()
    return any(identity.casefold() == submitted for identity in identities)
