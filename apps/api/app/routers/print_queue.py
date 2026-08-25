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

from app.db import get_db
from app.deps import get_current_user
from app.models.printer import Printer
from app.printers.job_control import (
    JOB_STATE_PENDING_HELD,
    JobControlError,
    set_cups_job_priority,
)
from app.printers.print_queue import (
    NORMAL_PRIORITY,
    YIELDED_PRIORITY,
    QueuedJob,
    queue_order,
    queued_jobs,
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


def _state_label(job: QueuedJob) -> str:
    if job.is_printing:
        return "printing"
    if job.state == JOB_STATE_PENDING_HELD:
        return "held"
    return "waiting"


async def _my_queued_jobs(current_user: UserOut) -> list[QueuedJob]:
    jobs = await asyncio.to_thread(queued_jobs)
    if jobs is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=CUPSD_UNREACHABLE
        )
    return [job for job in jobs if job.belongs_to(*_identities(current_user))]


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

    identities = _identities(current_user)
    mine_by_printer = {job.printer_id for job in jobs if job.belongs_to(*identities)}
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
            mine = job.belongs_to(*identities)
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
                    can_restore=mine and job.is_waiting and job.is_yielded,
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


async def _reprioritise(cups_job_id: int, priority: int, current_user: UserOut) -> None:
    mine = await _my_queued_jobs(current_user)
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
    try:
        await asyncio.to_thread(set_cups_job_priority, cups_job_id, priority)
    except JobControlError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/jobs/{cups_job_id}/yield", status_code=status.HTTP_204_NO_CONTENT)
async def yield_job(cups_job_id: int, current_user: UserOut = Depends(get_current_user)):
    """Moves this person's own waiting job behind everything else in its
    queue — including jobs sent while it waits, which is what the button says
    and the honest consequence of a priority floor rather than a one-off
    swap."""
    await _reprioritise(cups_job_id, YIELDED_PRIORITY, current_user)


@router.post("/jobs/{cups_job_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
async def restore_job(cups_job_id: int, current_user: UserOut = Depends(get_current_user)):
    """Puts a yielded job back to the default priority, which returns it to
    its original place in the line — behind everything sent before it, ahead
    of everything sent after. It cannot be used to get further forward than
    that, which is why it isn't a queue-jump in disguise."""
    await _reprioritise(cups_job_id, NORMAL_PRIORITY, current_user)
