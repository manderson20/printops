"""Admin release path for any job PrintOps is holding — a second release
surface distinct from the self-service PIN kiosk (app/routers/release.py),
gated by JWT admin role instead of a PIN. Reuses the same delivery primitive
(app/printers/release.py:submit_released_job) as the kiosk.

It used to cover only quota holds, on the reasoning that the kiosk handles the
rest. The kiosk can only release a job to the person who sent it: it resolves a
PIN to a Google Workspace user and matches Job.submitted_by against that
address. So anyone the roster doesn't know, or who has no PIN yet, had a job
that nothing in PrintOps could release — including, routinely, a test page,
which is submitted through the printer's own queue precisely so it is treated
like a real job and is therefore held like one. The admin was told "request id
is ..." and left standing at a printer that was never going to print.

Nothing here weakens the quota hold: an over-quota job still cannot be released
by the person who sent it, only by an admin, which was always the point."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError, submit_released_job
from app.schemas.job import HeldJobReleaseIn, JobListOut, JobOut

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[JobListOut])
async def list_held_jobs(db: AsyncSession = Depends(get_db)):
    """Every job currently held, whatever is holding it. hold_reason rides
    along on JobOut so the UI can say which is which — an over-quota job and a
    job waiting at a release printer need different things from an admin."""
    stmt = (
        select(Job, Printer.name)
        .join(Printer, Job.printer_id == Printer.id)
        .where(Job.status == "held")
        .order_by(Job.created_at)
    )
    rows = (await db.execute(stmt)).all()
    return [
        JobListOut(**JobOut.model_validate(job).model_dump(), printer_name=printer_name)
        for job, printer_name in rows
    ]


@router.post("/{job_id}/release", response_model=JobOut)
async def release_held_job(
    job_id: UUID,
    payload: HeldJobReleaseIn | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Releases one held job, optionally at a printer of the admin's choosing.

    The target defaults to the printer the job was sent to, which is the right
    answer for everything except a Follow-Me job: that was addressed to a
    virtual queue with no device behind it, and the kiosk resolves it to
    whichever printer the person is standing at. An admin isn't standing
    anywhere, so they have to say."""
    job = await db.get(Job, job_id)
    if job is None or job.status != "held":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Held job not found.")

    target_id = payload.printer_id if payload else None
    printer = await db.get(Printer, target_id or job.printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found.")
    if printer.is_virtual:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This job was sent to a Follow-Me queue, which has no printer behind it. "
                "Choose the printer to release it at."
            ),
        )

    try:
        await asyncio.to_thread(
            submit_released_job,
            str(printer.id),
            job.held_file_path,
            job.document_name,
            job.copy_count,
            job.held_job_options,
            # The release queue has no PrintOps backend on it, so this is the
            # only place the media-col workaround can be applied to a job that
            # was held — and it keys off the printer it is being released AT.
            bool((printer.capabilities or {}).get("media_col_broken")),
        )
    except ReleaseError as exc:
        job.status = "failed"
        job.error_message = str(exc)
        job.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(job)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Handed to CUPS for delivery — see submit_released_job's docstring for
    # why this can't wait for physical-print confirmation the way a normal
    # job's own backend invocation can (same caveat as the PIN kiosk's
    # release_job in app/routers/release.py).
    job.status = "forwarded"
    job.completed_at = datetime.now(UTC)
    if job.held_file_path:
        Path(job.held_file_path).unlink(missing_ok=True)
    job.held_file_path = None
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/discard", response_model=JobOut)
async def discard_held_job(job_id: UUID, db: AsyncSession = Depends(get_db)):
    """Throws one held job away without printing it.

    The deliberate counterpart to release. A hold otherwise sits until someone
    releases it or Job.held_expires_at passes and the sweep in app/main.py
    clears it — which is the right default, but leaves an admin no way to clear
    a job nobody is coming back for (a duplicate sent three times, a document
    submitted to the wrong printer) without waiting out the expiry.

    Disposes of it exactly as that sweep does, so there is one meaning of a
    discarded hold rather than two: the spool file is deleted, the row is kept
    and marked cancelled. The job stays in history and in the reports — what is
    destroyed is the document, not the record of it. That asymmetry is the
    whole point, and it is why this cannot be a DELETE.
    """
    # Claimed with a conditional UPDATE rather than read-then-write, because
    # this races the automatic release of printer_offline holds: the status
    # loop (app/printers/offline_holds.py) submits a held document the moment
    # its printer answers, in another session. Both could see status == "held"
    # and act — one printing the document while the history says it was
    # discarded, or the release failing on a file this handler already
    # deleted. Whoever moves the row out of "held" first wins; the loser is
    # told so and touches nothing.
    now = datetime.now(UTC)
    # Read before the claim: RETURNING hands back the *new* row, and the claim
    # below nulls this column, so asking for it there returns None and the
    # document is never deleted. Only these paths ever write it.
    existing = await db.get(Job, job_id)
    spool_path = existing.held_file_path if existing else None

    claimed = await db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "held")
        .values(
            status="cancelled",
            error_message="Discarded without printing",
            completed_at=now,
            held_file_path=None,
        )
        .returning(Job.id)
    )
    row = claimed.first()
    if row is None:
        await db.rollback()
        job = await db.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Held job not found.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This job is no longer held — it is {job.status}. "
                "It was most likely released while you were looking at it."
            ),
        )

    # The file is removed only after the claim is committed. Deleting first
    # would destroy the document of a job this handler turned out not to own.
    await db.commit()
    if spool_path:
        Path(spool_path).unlink(missing_ok=True)

    job = await db.get(Job, job_id)
    await db.refresh(job)
    return job
