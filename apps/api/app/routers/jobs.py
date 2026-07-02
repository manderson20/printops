from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import verify_backend_token
from app.models.job import Job
from app.schemas.job import JobCreate, JobOut, JobUpdate

router = APIRouter(dependencies=[Depends(verify_backend_token)])


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
