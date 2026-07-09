import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.resolve import resolve_user
from app.db import get_db
from app.deps import get_current_user, require_role, verify_backend_token
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.models.printer import Printer
from app.models.release import PrintReleaseSettings
from app.printers.job_control import JobControlError, cancel_cups_job
from app.quotas.service import resolve_hold_reason
from app.reports.aggregation import resolve_device_names
from app.schemas.auth import UserOut
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
    device_names = await resolve_device_names(
        db, {job.mac_address for job, _ in rows if job.mac_address}
    )
    return [
        JobListOut(
            **JobOut.model_validate(job).model_dump(),
            printer_name=printer_name,
            device_name=device_names.get(job.mac_address) if job.mac_address else None,
        )
        for job, printer_name in rows
    ]


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script right before it attempts delivery.

    hold_reason is decided here — once, using submitted_by as already
    resolved a few lines up — rather than by the script itself. The script
    (infra/cups/backends/printops) only reacts to whatever's returned here:
    if hold_reason is set, it spools the document and PATCHes this job to
    status="held" instead of forwarding, the same mechanics it already had
    for a release_required printer, just triggered by this field now instead
    of a separate printer.release_required lookup. See
    app/quotas/service.py:resolve_hold_reason for "pin_release" vs "quota"."""
    attributed_user, attribution_method, mac_address = await resolve_user(
        db, payload.submitted_by, payload.source_host
    )
    printer = await db.get(Printer, payload.printer_id)
    hold_reason = await resolve_hold_reason(db, printer, attributed_user) if printer else None
    job = Job(
        printer_id=payload.printer_id,
        cups_job_id=payload.cups_job_id,
        submitted_by=attributed_user,
        attribution_method=attribution_method,
        mac_address=mac_address,
        file_size_bytes=payload.file_size_bytes,
        document_name=payload.document_name,
        copy_count=payload.copy_count,
        status="forwarding",
        hold_reason=hold_reason,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def _get_or_create_print_release_settings(db: AsyncSession) -> PrintReleaseSettings:
    result = await db.execute(select(PrintReleaseSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = PrintReleaseSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(job_id: UUID, payload: JobUpdate, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script to report the forwarding outcome —
    or, for a release_required printer, to record that a job is held
    instead of forwarded (see infra/cups/backends/printops and
    app/routers/release.py)."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    job.status = payload.status
    job.error_message = payload.error_message
    if payload.file_size_bytes is not None:
        job.file_size_bytes = payload.file_size_bytes
    job.page_count = payload.page_count
    job.color_mode = payload.color_mode
    job.duplex = payload.duplex
    job.paper_size = payload.paper_size

    if payload.status == "held":
        job.held_file_path = payload.held_file_path
        job.held_job_options = payload.held_job_options
        # Computed server-side, never trusted from the backend script's own
        # clock (see JobUpdate's docstring).
        release_settings = await _get_or_create_print_release_settings(db)
        hold_hours = release_settings.hold_expiry_hours
        job.held_expires_at = datetime.now(UTC) + timedelta(hours=hold_hours)
    else:
        # "forwarded"/"failed" are the only other statuses this endpoint
        # ever sets — both terminal (see Job.status's docstring), so this
        # call always marks completion for them.
        job.completed_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(job)
    return job


@user_router.post(
    "/{job_id}/cancel", response_model=JobOut, dependencies=[Depends(require_role("admin"))]
)
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    """Cancels a single in-flight job — only valid while it's still
    "forwarding" (see Job.status): forwarded/failed/cancelled are already
    terminal, and cancelling something that never started makes no sense.
    For a printer backed up with jobs PrintOps can't individually see yet,
    see POST /printers/{id}/purge-jobs instead."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != "forwarding":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is already {job.status} — only in-flight jobs can be cancelled.",
        )
    if job.cups_job_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has no CUPS job id on record — nothing to cancel.",
        )
    try:
        await asyncio.to_thread(cancel_cups_job, job.cups_job_id)
    except JobControlError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    job.status = "cancelled"
    job.error_message = f"Cancelled by {current_user.username}"
    job.completed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(job)
    return job


@user_router.get(
    "/usage", response_model=list[UserUsageOut], dependencies=[Depends(require_role("admin"))]
)
async def list_job_usage(db: AsyncSession = Depends(get_db)):
    """Per-user page/byte totals, one row per synced Google Workspace
    roster user — including roster users who have never printed (zero
    totals) — so this reads as an org roster report, not just a list of
    whoever happened to submit a job. Anything printed under a name/email
    that isn't in the roster (attribution_method "unresolved", or a local
    username ClassGuard/Mosyle/Google Workspace never resolved to a
    roster address) is rolled into a single trailing `is_other` row
    rather than dropped, so admins keep visibility into that volume.
    Admin-only since it aggregates data across every user, not just the
    caller."""
    roster = (
        (await db.execute(select(GoogleWorkspaceUser).order_by(GoogleWorkspaceUser.email)))
        .scalars()
        .all()
    )

    job_stats_stmt = select(
        func.lower(Job.submitted_by).label("email_key"),
        func.count(Job.id).label("job_count"),
        func.coalesce(func.sum(Job.page_count), 0).label("total_pages"),
        func.coalesce(func.sum(Job.file_size_bytes), 0).label("total_bytes"),
    ).group_by(func.lower(Job.submitted_by))
    job_stats = {row.email_key: row for row in (await db.execute(job_stats_stmt)).all()}

    roster_emails = {u.email.lower() for u in roster}
    rows = []
    for user in roster:
        stats = job_stats.get(user.email.lower())
        rows.append(
            UserUsageOut(
                email=user.email,
                name=user.name,
                job_count=stats.job_count if stats else 0,
                total_pages=stats.total_pages if stats else 0,
                total_bytes=stats.total_bytes if stats else 0,
            )
        )
    rows.sort(key=lambda r: r.total_pages, reverse=True)

    other_job_count = other_pages = other_bytes = 0
    for email_key, stats in job_stats.items():
        if email_key not in roster_emails:
            other_job_count += stats.job_count
            other_pages += stats.total_pages
            other_bytes += stats.total_bytes
    if other_job_count:
        rows.append(
            UserUsageOut(
                email=None,
                name=None,
                is_other=True,
                job_count=other_job_count,
                total_pages=other_pages,
                total_bytes=other_bytes,
            )
        )

    return rows
