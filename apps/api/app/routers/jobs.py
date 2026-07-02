from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, verify_backend_token
from app.models.job import Job
from app.models.printer import Printer
from app.schemas.job import JobCreate, JobListOut, JobOut, JobUpdate

router = APIRouter(dependencies=[Depends(verify_backend_token)])

# Separate router: these endpoints are read by logged-in admins (JWT), not
# the CUPS backend script (X-Backend-Token) — different trust boundary, same
# split already used between printers.py and internal.py.
user_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_router.get("", response_model=list[JobListOut])
async def list_jobs(
    printer_id: UUID | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Job, Printer.name)
        .join(Printer, Job.printer_id == Printer.id)
        .order_by(Job.created_at.desc())
        .limit(min(limit, 200))
    )
    if printer_id is not None:
        stmt = stmt.where(Job.printer_id == printer_id)
    rows = (await db.execute(stmt)).all()
    return [
        JobListOut(**JobOut.model_validate(job).model_dump(), printer_name=printer_name)
        for job, printer_name in rows
    ]


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script right before it attempts delivery."""
    job = Job(
        printer_id=payload.printer_id,
        cups_job_id=payload.cups_job_id,
        submitted_by=payload.submitted_by,
        file_size_bytes=payload.file_size_bytes,
        status="forwarding",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(job_id: UUID, payload: JobUpdate, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script to report the forwarding outcome."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job.status = payload.status
    job.error_message = payload.error_message
    await db.commit()
    await db.refresh(job)
    return job
