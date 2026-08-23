"""Admin release path for jobs held over a page quota (Job.hold_reason ==
"quota") — a second, admin-only release surface distinct from the
self-service PIN kiosk (app/routers/release.py), which is restricted to
hold_reason="pin_release" jobs only (see release.py's query filters). Reuses
the same delivery primitive (app/printers/release.py:submit_released_job) as
the kiosk, just gated by JWT admin role instead of a PIN."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError, submit_released_job
from app.schemas.job import JobListOut, JobOut

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[JobListOut])
async def list_quota_holds(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Job, Printer.name)
        .join(Printer, Job.printer_id == Printer.id)
        .where(Job.status == "held", Job.hold_reason == "quota")
        .order_by(Job.created_at)
    )
    rows = (await db.execute(stmt)).all()
    return [
        JobListOut(**JobOut.model_validate(job).model_dump(), printer_name=printer_name)
        for job, printer_name in rows
    ]


@router.post("/{job_id}/release", response_model=JobOut)
async def release_quota_hold(job_id: UUID, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if job is None or job.status != "held" or job.hold_reason != "quota":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quota-held job not found."
        )

    printer = await db.get(Printer, job.printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found.")

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
