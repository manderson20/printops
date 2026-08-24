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
from app.printers import offline_holds
from app.printers.capabilities import recorded_color_mode
from app.printers.job_control import JobControlError, cancel_cups_job
from app.quotas.service import resolve_hold_reason
from app.reports.aggregation import (
    ReportFilters,
    get_cost_raw_rows,
    resolve_device_names,
    resolve_display_names,
)
from app.reports.cost_rates import load_printer_rates
from app.reports.formulas import FormulaValues, job_cost
from app.schemas.auth import UserOut
from app.schemas.job import JobCreate, JobListOut, JobOut, JobUpdate, UserUsageOut, UserUsagePage

router = APIRouter(dependencies=[Depends(verify_backend_token)])

# Separate router: these endpoints are read by logged-in admins (JWT), not
# the CUPS backend script (X-Backend-Token) — different trust boundary, same
# split already used between printers.py and internal.py.
user_router = APIRouter(dependencies=[Depends(get_current_user)])


@user_router.get("", response_model=list[JobListOut], dependencies=[Depends(require_role("admin"))])
async def list_jobs(
    printer_id: UUID | None = None,
    submitted_by: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Fleet-wide, unfiltered — every job from every user, no submitted_by
    scoping (contrast app/routers/reports.py's _report_filters, which
    force-scopes a viewer to their own history for Insights). Admin-only
    for that reason; a viewer's own print history is available via
    Insights instead. Only frontend callers are admin-only pages (Jobs,
    the printer detail Jobs tab, Usage) — see apps/web's dashboard layout
    NAV_LINKS."""
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
    submitted_by_names = await resolve_display_names(
        db, {job.submitted_by for job, _ in rows if job.submitted_by}
    )
    return [
        JobListOut(
            **JobOut.model_validate(job).model_dump(),
            printer_name=printer_name,
            device_name=device_names.get(job.mac_address) if job.mac_address else None,
            submitted_by_name=submitted_by_names.get(job.submitted_by)
            if job.submitted_by
            else None,
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
        cups_job_uuid=payload.cups_job_uuid,
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
    await _supersede_earlier_attempts(db, job)
    await db.commit()
    await db.refresh(job)
    return job


# How far back an earlier row for the same CUPS job is treated as an earlier
# attempt at it. cupsd's retries arrive seconds to minutes apart, so this is
# generous; the bound exists because CUPS job numbers are not unique forever —
# they reset when the spool is cleared, and an unrelated job inheriting an old
# number must not reach back and rewrite a stranger's history.
SUPERSEDES_WITHIN = timedelta(hours=6)


async def _supersede_earlier_attempts(db: AsyncSession, job: Job) -> None:
    """Closes out earlier rows for the CUPS job this one is retrying.

    cupsd re-runs the backend for every retry of a job, and the backend
    registers a new row each time, so one CUPS job can leave a trail. Job 4584
    on the ES Veronica Copier left 51 of them in August, every one of them
    recorded as a failure — so the reports showed 51 failed jobs where a person
    had sent one. The failure count is the number an admin acts on, and it was
    wrong by a factor of fifty.

    Earlier attempts are marked cancelled with the reason spelled out, not
    failed: what failed is the delivery of one job, and the last row in the
    trail is the one that says how that ended. A row already recorded as
    `forwarded` is left alone — that is a delivery that happened, whatever
    cupsd did with the job number afterwards.

    app/printers/job_reconcile.py does the same thing from the other end, for
    rows whose backend never reported at all."""
    if job.cups_job_id is None and job.cups_job_uuid is None:
        return
    # Flushed first so the row has its id: without it `Job.id != job.id`
    # compares against None, which SQLAlchemy renders as `id IS NOT NULL` —
    # and the autoflush that the query below triggers anyway would then have
    # this job supersede itself. test_create_and_update_job caught exactly
    # that.
    await db.flush()
    if job.cups_job_uuid is not None:
        # CUPS's own identifier, so no time bound is needed or wanted: it is
        # generated per job and never reused, and two rows carrying it are two
        # attempts at the same job however far apart they are.
        identity = [Job.cups_job_uuid == job.cups_job_uuid]
    else:
        # A backend that predates job-uuid, or a job CUPS gave none for. The
        # number resets when the spool is cleared, so this is bounded in time
        # to keep an unrelated job from inheriting an old one's history.
        identity = [
            Job.printer_id == job.printer_id,
            Job.cups_job_id == job.cups_job_id,
            Job.cups_job_uuid.is_(None),
            Job.created_at >= datetime.now(UTC) - SUPERSEDES_WITHIN,
        ]

    earlier = (
        (
            await db.execute(
                select(Job).where(
                    *identity,
                    Job.id != job.id,
                    Job.status.not_in(("forwarded", "held")),
                )
            )
        )
        .scalars()
        .all()
    )
    for previous in earlier:
        previous.status = "cancelled"
        previous.superseded_by_job_id = job.id
        previous.error_message = (
            "Superseded — the print server retried this job, and the later record of it "
            "has what became of it."
        )
        if previous.completed_at is None:
            previous.completed_at = datetime.now(UTC)


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
    # CUPS reports the mode the job asked for; a mono printer prints a colour
    # job in grey and says nothing. See recorded_color_mode's docstring.
    printer = await db.get(Printer, job.printer_id)
    job.color_mode = recorded_color_mode(
        printer.capabilities if printer else None, payload.color_mode
    )
    job.duplex = payload.duplex
    job.paper_size = payload.paper_size

    if payload.status == "held":
        job.held_file_path = payload.held_file_path
        job.held_job_options = payload.held_job_options
        if job.hold_reason == offline_holds.HOLD_REASON:
            # No expiry on a job that is only waiting for its printer to be
            # switched on. The release-hold expiry exists because a person who
            # never comes to collect their printing should not leave it on the
            # server forever — but nobody has failed to collect this one, and
            # the clock would run out while the printer is off for the very
            # weekend the hold exists to survive: held Friday, deleted Sunday
            # at the 48-hour default, printing Monday impossible. It goes when
            # the printer answers (app/printers/offline_holds.py).
            job.held_expires_at = None
        else:
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
    """Cancels a job's CUPS job — valid for anything that hasn't printed.

    Not just "forwarding", which is what this accepted until 0.68.0. PrintOps
    marks a job `failed` the moment its backend exits non-zero, but cupsd keeps
    the job on the queue and retries it: on 2026-08-24 the Graphic Arts Kyocera
    had a job that was `failed` in PrintOps and still on the queue eight hours
    later, stopping the printer every time cupsd tried it again. Terminal in our
    record is not the same as gone from the print server, and the admin looking
    at that row needs to be able to get rid of it.

    `forwarded` is the one status that is genuinely finished — the pages exist,
    there is nothing left to cancel. Held jobs are spooled by PrintOps rather
    than queued in CUPS and have their own Discard (see
    app/routers/held_jobs.py).

    For a printer backed up with jobs PrintOps can't individually see yet, see
    POST /printers/{id}/purge-jobs instead."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == "forwarded":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job has already printed — there is nothing left to cancel.",
        )
    if job.status == "held":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Held jobs are discarded rather than cancelled.",
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
    # scripts/cancel_cups_job.sh treats "already gone" as success, so this is
    # reached whether the job was still on the queue or had finished retrying
    # in between. Either way the admin's goal — this job is not coming back —
    # now holds, and saying so is more use than a 400 about which of the two
    # it was.
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
    "*example.com" -> only emails ending "@example.com", the
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
