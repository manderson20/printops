"""The line a person is standing in, and the one thing they may do about it.

Requested by the choir teacher, who sends 200-page sets and would rather they
waited than have everyone else's two-page handout stuck behind them. So the
only action here is yielding: a job can be moved behind everything else
waiting, and put back where it was. There is deliberately no way to move a job
*up*, because up means ahead of a colleague's job, and a queue-jump button is
still a queue-jump button when it is labelled politely.

A job that has started printing is not reorderable — cupsd would refuse
anyway, since the document is already on its way to the device — and neither
is anyone else's job, at any status.
"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.resolve import resolve_user
from app.db import get_db
from app.deps import get_current_user
from app.models.printer import Printer
from app.printers.job_control import (
    JOB_STATE_PENDING_HELD,
    JobControlError,
    set_cups_job_priority,
)
from app.printers.print_queue import (
    YIELDED_PRIORITY,
    QueuedJob,
    forget_priority_before_yield,
    priority_before_yield,
    queue_order,
    queued_jobs,
    remember_priority_before_yield,
)
from app.schemas.auth import UserOut
from app.schemas.print_queue import PrintQueueJobOut, PrintQueuePrinterOut

router = APIRouter(dependencies=[Depends(get_current_user)])

CUPSD_UNREACHABLE = "The print server didn't answer when asked what is queued. Try again shortly."


def _identities(current_user: UserOut) -> tuple[str | None, ...]:
    """Every name this person's jobs could have been submitted under. For an
    SSO login `username` *is* the attributed email (see app/schemas/auth.py),
    which is what CUPS records, and the same convention Insights scopes on
    (app/routers/reports.py:_report_filters)."""
    return (current_user.username, current_user.email)


class _Ownership:
    """Decides which of these queued jobs belong to the person looking.

    A direct name match settles most of them, but not all: CUPS records
    whatever the client sent, and a Mac commonly sends a bare local account
    name — "matt", not the address the person signs in with. PrintOps already
    has the machinery for that (app/attribution/resolve.py, which disambiguates
    a bare username via the device that sent the job, then aliases, then the
    Workspace roster) and it is the same machinery that decides whose job it is
    everywhere else, so the queue must not answer this question its own way.
    Without it those people see an empty page and get a 404 from yield on a job
    that is sitting right there under their name.

    Resolution is per distinct (owner, host) pair and cached for the request:
    the fallback path scans the roster, and a queue has a handful of distinct
    senders, not a handful per job."""

    def __init__(self, db: AsyncSession, current_user: UserOut) -> None:
        self._db = db
        self._identities = _identities(current_user)
        self._cache: dict[tuple[str | None, str | None], bool] = {}

    async def owns(self, job: QueuedJob) -> bool:
        # QueuedJob.belongs_to is the direct comparison, including the "cupsd
        # gave us no owner, so this belongs to nobody" case.
        if job.belongs_to(*self._identities):
            return True
        if not job.owner:
            return False
        key = (job.owner, job.source_host)
        if key not in self._cache:
            attributed, _method, _mac = await resolve_user(self._db, job.owner, job.source_host)
            self._cache[key] = any(
                identity and identity.casefold() == attributed.casefold()
                for identity in self._identities
            )
        return self._cache[key]

    async def filter(self, jobs: list[QueuedJob]) -> list[QueuedJob]:
        return [job for job in jobs if await self.owns(job)]


def _state_label(job: QueuedJob) -> str:
    if job.is_printing:
        return "printing"
    if job.state == JOB_STATE_PENDING_HELD:
        return "held"
    return "waiting"


async def _my_queued_jobs(db: AsyncSession, current_user: UserOut) -> list[QueuedJob]:
    jobs = await asyncio.to_thread(queued_jobs)
    if jobs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CUPSD_UNREACHABLE
        )
    return await _Ownership(db, current_user).filter(jobs)


@router.get("", response_model=list[PrintQueuePrinterOut])
async def get_my_print_queue(
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """The queues this person currently has something in, each shown whole.

    Only those queues: a printer they have nothing waiting on tells them
    nothing they can act on, and listing every printer in the district would
    turn a page about their own job into a fleet monitor. Within a queue they
    see every job, because "am I holding anyone up?" cannot be answered from
    their own jobs alone — but other people's appear as a size and a place in
    line, never a document name."""
    jobs = await asyncio.to_thread(queued_jobs)
    if jobs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CUPSD_UNREACHABLE
        )

    ownership = _Ownership(db, current_user)
    mine_by_printer = {job.printer_id for job in await ownership.filter(jobs)}
    mine_by_printer.discard(None)
    if not mine_by_printer:
        return []

    result = await db.execute(
        select(Printer).where(Printer.id.in_([UUID(pid) for pid in mine_by_printer]))
    )
    printers = {str(printer.id): printer for printer in result.scalars().all()}

    queues: list[PrintQueuePrinterOut] = []
    for printer_id in mine_by_printer:
        printer = printers.get(printer_id)
        if printer is None:
            # A queue whose printer row has been archived out from under it.
            # Nothing useful to show, and no name to show it under.
            continue
        on_this_printer = sorted(
            (job for job in jobs if job.printer_id == printer_id), key=queue_order
        )
        rows = []
        for position, job in enumerate(on_this_printer, start=1):
            mine = await ownership.owns(job)
            rows.append(
                PrintQueueJobOut(
                    cups_job_id=job.cups_job_id,
                    position=position,
                    mine=mine,
                    document_name=job.document_name if mine else None,
                    size_bytes=job.size_bytes,
                    state=_state_label(job),
                    yielded=job.is_yielded,
                    # Yielding is for a job that is still waiting its turn.
                    # Once it is printing there is nothing left to reorder,
                    # and a held job is waiting on a person, not on the queue.
                    can_yield=mine and job.is_waiting and not job.is_yielded,
                    # Only a job PrintOps itself lowered can be raised again,
                    # so a client that submitted its own low priority is never
                    # quietly promoted past the jobs it was queued behind.
                    can_restore=(
                        mine
                        and job.is_waiting
                        and job.is_yielded
                        and priority_before_yield(job.printer_id, job.cups_job_id) is not None
                    ),
                    submitted_at=job.created_at,
                )
            )
        queues.append(
            PrintQueuePrinterOut(
                printer_id=printer.id,
                printer_name=printer.name,
                jobs=rows,
                my_job_count=sum(1 for row in rows if row.mine),
                total_job_count=len(rows),
            )
        )
    return sorted(queues, key=lambda queue: queue.printer_name)


async def _reprioritise(
    cups_job_id: int, yielding: bool, db: AsyncSession, current_user: UserOut
) -> None:
    mine = await _my_queued_jobs(db, current_user)
    job = next((candidate for candidate in mine if candidate.cups_job_id == cups_job_id), None)
    if job is None:
        # Deliberately the same answer for "no such job", "someone else's job"
        # and "already printed": a 403 that distinguishes them would confirm
        # the existence of other people's jobs to anyone who guessed an id.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That job isn't waiting in a queue under your name.",
        )
    if job.is_printing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job has already started printing, so it can't be moved.",
        )
    if not job.is_waiting:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is being held rather than waiting its turn, so it can't be moved.",
        )
    if yielding:
        priority = YIELDED_PRIORITY
    else:
        # Back to what it was before PrintOps lowered it, not to the default:
        # assuming 50 would raise a job that arrived at some other priority
        # past the jobs it was legitimately queued behind.
        priority = priority_before_yield(job.printer_id, cups_job_id)
        if priority is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "PrintOps no longer knows where this job was before it moved back, so it "
                    "can't be put back in line. It will still print, after the jobs ahead of it."
                ),
            )

    try:
        await asyncio.to_thread(set_cups_job_priority, cups_job_id, priority)
    except JobControlError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if yielding:
        remember_priority_before_yield(job.printer_id, cups_job_id, job.priority)
    else:
        forget_priority_before_yield(job.printer_id, cups_job_id)


@router.post("/jobs/{cups_job_id}/yield", status_code=status.HTTP_204_NO_CONTENT)
async def yield_job(
    cups_job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Moves this person's own waiting job behind everything else in its
    queue — including jobs sent while it waits, which is what the button says
    and the honest consequence of a priority floor rather than a one-off
    swap."""
    await _reprioritise(cups_job_id, True, db, current_user)


@router.post("/jobs/{cups_job_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_job(
    cups_job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Puts a yielded job back to the default priority, which returns it to
    its original place in the line — behind everything sent before it, ahead
    of everything sent after. It cannot be used to get further forward than
    that, which is why it isn't a queue-jump in disguise."""
    await _reprioritise(cups_job_id, False, db, current_user)
