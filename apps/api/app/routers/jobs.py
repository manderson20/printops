from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.resolve import resolve_user
from app.db import get_db
from app.deps import get_current_user, require_role, verify_backend_token
from app.models.job import Job
from app.models.printer import Printer
from app.schemas.job import JobCreate, JobListOut, JobOut, JobUpdate, UserUsageOut

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
    attributed_user, attribution_method = await resolve_user(
        db, payload.submitted_by, payload.source_host
    )
    job = Job(
        printer_id=payload.printer_id,
        cups_job_id=payload.cups_job_id,
        submitted_by=attributed_user,
        attribution_method=attribution_method,
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
    job.page_count = payload.page_count
    await db.commit()
    await db.refresh(job)
    return job


@user_router.get(
    "/usage", response_model=list[UserUsageOut], dependencies=[Depends(require_role("admin"))]
)
async def list_job_usage(db: AsyncSession = Depends(get_db)):
    """Per-user page/byte totals across all logged jobs — admin-only since it
    aggregates data across every user, not just the caller."""
    stmt = (
        select(
            Job.submitted_by,
            func.count(Job.id).label("job_count"),
            func.coalesce(func.sum(Job.page_count), 0).label("total_pages"),
            func.coalesce(func.sum(Job.file_size_bytes), 0).label("total_bytes"),
        )
        .group_by(Job.submitted_by)
        .order_by(func.coalesce(func.sum(Job.page_count), 0).desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        UserUsageOut(
            submitted_by=row.submitted_by,
            job_count=row.job_count,
            total_pages=row.total_pages,
            total_bytes=row.total_bytes,
        )
        for row in rows
    ]
