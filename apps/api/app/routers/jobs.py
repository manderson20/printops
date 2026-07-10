import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.resolve import resolve_user
from app.db import get_db
from app.deps import get_current_user, require_role, verify_backend_token
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.models.printer import Printer
from app.models.release import PrintReleaseSettings
from app.models.report import ReportFormulaSettings
from app.printers.job_control import JobControlError, cancel_cups_job
from app.quotas.service import resolve_hold_reason
from app.reports.aggregation import ReportFilters, get_cost_raw_rows, resolve_device_names
from app.reports.cost_rates import load_printer_rates
from app.reports.formulas import FormulaValues, job_cost
from app.schemas.auth import UserOut
from app.schemas.job import JobCreate, JobListOut, JobOut, JobUpdate, UserUsageOut, UserUsagePage

router = APIRouter(dependencies=[Depends(verify_backend_token)])

# Separate router: these endpoints are read by logged-in admins (JWT), not
# the CUPS backend script (X-Backend-Token) — different trust boundary, same
# split already used between printers.py and internal.py.
user_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_router.get("", response_model=list[JobListOut])
async def list_jobs(
    printer_id: UUID | None = None,
    submitted_by: str | None = None,
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
    if submitted_by is not None:
        stmt = stmt.where(func.lower(Job.submitted_by) == submitted_by.lower())
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
    if printer is not None and printer.archived_at is not None:
        # Belt-and-suspenders — archive_printer already tore down this
        # printer's CUPS queue, so the backend script realistically can't
        # reach this endpoint for it at all. Guards the same race any
        # queue-removal-based enforcement has: a job already in flight
        # when the archive happened.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This printer has been archived and no longer accepts jobs.",
        )
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


@dataclass
class _UsageAccumulator:
    """Running per-user totals built one job row at a time — same
    one-pass-over-CostRawRow approach as app/routers/reports.py's
    _CostAccumulator, since accurate cost needs each job's own printer
    rate and physical-sheet rounding, not a single SQL aggregate (see
    app/reports/aggregation.py:get_cost_raw_rows's docstring)."""

    job_count: int = 0
    total_pages: int = 0
    total_bytes: int = 0
    duplex_pages: int = 0
    simplex_pages: int = 0
    mono_pages: int = 0
    color_pages: int = 0
    estimated_cost: float = 0.0


async def _get_or_create_usage_formula_settings(db: AsyncSession) -> ReportFormulaSettings:
    result = await db.execute(select(ReportFormulaSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ReportFormulaSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def _usage_accumulators_by_email(db: AsyncSession) -> dict[str | None, _UsageAccumulator]:
    """One pass over every job ever logged, keyed by lowercased
    submitted_by (None for jobs with no attribution at all) — mirrors
    list_job_usage's previous SQL GROUP BY grouping exactly, just computed
    in Python so real per-job cost (job_cost, priced off each job's own
    printer's toner rate) can be accumulated alongside the simpler
    page/byte/duplex/color sums in the same loop."""
    raw_rows = await get_cost_raw_rows(db, ReportFilters())
    formula_settings = await _get_or_create_usage_formula_settings(db)
    fallback = FormulaValues(
        cost_per_page_mono=formula_settings.cost_per_page_mono,
        cost_per_page_color=formula_settings.cost_per_page_color,
        sheets_per_tree=formula_settings.sheets_per_tree,
        co2_grams_per_sheet=formula_settings.co2_grams_per_sheet,
    )
    printer_ids = {row.printer_id for row in raw_rows}
    rates = await load_printer_rates(db, printer_ids, fallback)

    accumulators: dict[str | None, _UsageAccumulator] = {}
    for row in raw_rows:
        key = row.submitted_by.lower() if row.submitted_by else None
        acc = accumulators.setdefault(key, _UsageAccumulator())
        acc.job_count += 1
        acc.total_pages += row.page_count
        acc.total_bytes += row.file_size_bytes or 0
        # Unknown duplex/color treated as simplex/mono — same conservative
        # fallback app/reports/formulas.py's compute_environmental_impact
        # and job_cost already use.
        if row.duplex:
            acc.duplex_pages += row.page_count
        else:
            acc.simplex_pages += row.page_count
        if row.color_mode == "color":
            acc.color_pages += row.page_count
        else:
            acc.mono_pages += row.page_count

        rate = rates[row.printer_id]
        cost = job_cost(
            row.page_count, row.color_mode, row.duplex, rate, formula_settings.cost_per_sheet_paper
        )
        acc.estimated_cost += cost.total_cost

    return accumulators


def _usage_out(
    email: str | None, name: str | None, is_other: bool, acc: _UsageAccumulator | None
) -> UserUsageOut:
    acc = acc or _UsageAccumulator()
    return UserUsageOut(
        email=email,
        name=name,
        is_other=is_other,
        job_count=acc.job_count,
        total_pages=acc.total_pages,
        total_bytes=acc.total_bytes,
        duplex_pages=acc.duplex_pages,
        simplex_pages=acc.simplex_pages,
        mono_pages=acc.mono_pages,
        color_pages=acc.color_pages,
        estimated_cost=round(acc.estimated_cost, 2),
    )


def _usage_row_matches(row: UserUsageOut, search: str) -> bool:
    """Supports a leading "*" for a domain-suffix filter (e.g.
    "*brookfieldr3.org" -> only emails ending "@brookfieldr3.org", the
    quickest way to split staff from students when the two groups use
    different domains) — otherwise a plain substring match against
    name/email, same as the frontend's previous client-side filter."""
    if search.startswith("*"):
        suffix = search[1:].strip().lower()
        if suffix and not suffix.startswith("@"):
            suffix = "@" + suffix
        return bool(row.email) and row.email.lower().endswith(suffix)
    query = search.lower()
    if row.is_other:
        haystack = "other unattributed"
    else:
        haystack = f"{row.name or ''} {row.email or ''}".lower()
    return query in haystack


@user_router.get(
    "/usage", response_model=UserUsagePage, dependencies=[Depends(require_role("admin"))]
)
async def list_job_usage(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Per-user usage totals, one row per synced Google Workspace roster
    user — including roster users who have never printed (zero totals) —
    so this reads as an org roster report, not just a list of whoever
    happened to submit a job. Anything printed under a name/email that
    isn't in the roster (attribution_method "unresolved", or a local
    username ClassGuard/Mosyle/Google Workspace never resolved to a
    roster address) is rolled into a single trailing `is_other` row rather
    than dropped, so admins keep visibility into that volume. Admin-only
    since it aggregates data across every user, not just the caller."""
    roster = (
        (await db.execute(select(GoogleWorkspaceUser).order_by(GoogleWorkspaceUser.email)))
        .scalars()
        .all()
    )
    accumulators = await _usage_accumulators_by_email(db)
    roster_emails = {u.email.lower() for u in roster}

    rows = [_usage_out(u.email, u.name, False, accumulators.get(u.email.lower())) for u in roster]
    rows.sort(key=lambda r: r.total_pages, reverse=True)

    other_acc = _UsageAccumulator()
    for email_key, acc in accumulators.items():
        if email_key not in roster_emails:
            other_acc.job_count += acc.job_count
            other_acc.total_pages += acc.total_pages
            other_acc.total_bytes += acc.total_bytes
            other_acc.duplex_pages += acc.duplex_pages
            other_acc.simplex_pages += acc.simplex_pages
            other_acc.mono_pages += acc.mono_pages
            other_acc.color_pages += acc.color_pages
            other_acc.estimated_cost += acc.estimated_cost
    if other_acc.job_count:
        rows.append(_usage_out(None, None, True, other_acc))

    if search:
        rows = [r for r in rows if _usage_row_matches(r, search)]

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    return UserUsagePage(items=page_rows, total=total, page=page, page_size=page_size)


@user_router.get(
    "/usage/{email}", response_model=UserUsageOut, dependencies=[Depends(require_role("admin"))]
)
async def get_user_usage(email: str, db: AsyncSession = Depends(get_db)):
    """One roster user's usage totals — the per-user drill-down from the
    Usage page (GET /jobs/usage). 404s for anything not a synced roster
    address, same scoping as that list (there's no single identity behind
    the `is_other` bucket to drill into)."""
    result = await db.execute(
        select(GoogleWorkspaceUser).where(func.lower(GoogleWorkspaceUser.email) == email.lower())
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No synced Google Workspace user with this email.",
        )
    accumulators = await _usage_accumulators_by_email(db)
    return _usage_out(user.email, user.name, False, accumulators.get(user.email.lower()))
