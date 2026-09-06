"""Print-and-release kiosk API — deliberately a third trust boundary,
distinct from both the user-JWT routers (app/deps.py:get_current_user) and
the CUPS backend-token routers (verify_backend_token). There is no
`Depends` auth on this router at all: a kiosk page is loaded by anyone who
knows a printer's release URL (an unguessable token, not a login), and
every mutating call re-validates a PIN (Google Workspace Employee ID)
itself, scoped to that one printer. No session/cookie is issued — the
kiosk re-sends the PIN on every action, so there's nothing on this side to
"log out" of between different people using the same physical kiosk. The
kiosk's own Done button is therefore purely a client-side matter of
forgetting the PIN it is holding — there is no server session to end, and
no endpoint here for it to call.

Since this is reachable over the network (unlike a physical copier
touchscreen), repeated-guess PIN attempts are rate-limited per printer
token — app/core/rate_limit.py, shared with the admin sign-in, in-memory
and fine for this app's single uvicorn worker process."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.db import get_db
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.job import Job
from app.models.printer import Printer
from app.printers.release import ReleaseError, submit_released_job
from app.schemas.release import HeldJobOut, HeldJobsOut, ReleasePinRequest

router = APIRouter()

# Same budget the admin sign-in gets, from the same limiter — one place
# that knows how a sliding window of failures behaves, rather than two
# that have to be kept in step.
_rate_limiter = RateLimiter(
    max_failures=8,
    window_seconds=300,
    message="Too many incorrect PIN attempts — try again in a few minutes.",
)


async def _get_printer_by_token(db: AsyncSession, token: str) -> Printer:
    result = await db.execute(select(Printer).where(Printer.release_token == token))
    printer = result.scalar_one_or_none()
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Release link not found.")
    return printer


async def _resolve_pin(db: AsyncSession, token: str, pin: str) -> GoogleWorkspaceUser:
    _rate_limiter.check(token)
    stmt = select(GoogleWorkspaceUser).where(GoogleWorkspaceUser.employee_id == pin)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        _rate_limiter.record_failure(token)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN not recognized.")
    _rate_limiter.record_success(token)
    return user


def _releasable_here(printer: Printer):
    """This person's held jobs releasable at this one kiosk: their
    pin_release jobs originally sent to this exact printer (the original
    "specific URL per printer" design), plus — only if this printer has
    opted into the follow-me pool — their follow_me jobs from *any*
    printer. A printer that hasn't opted in doesn't surface pool jobs, even
    though the token/kiosk page is shared. Restricted to these two
    hold_reason values — a job held for being over a page quota
    (app/routers/quota_holds.py) must never appear here, since the whole
    point of that hold is that only an admin can release it."""
    conditions = [(Job.hold_reason == "pin_release") & (Job.printer_id == printer.id)]
    if printer.follow_me_enabled:
        conditions.append(Job.hold_reason == "follow_me")
    return or_(*conditions)


async def _attach_printer_names(
    db: AsyncSession, printer: Printer, jobs: list[Job]
) -> list[HeldJobOut]:
    """Resolves the originating printer's name for any job not submitted to
    this kiosk's own printer (i.e. a follow_me job released elsewhere) —
    Job has no ORM relationship to Printer, so this is a small batch
    lookup rather than N+1 per-job queries."""
    other_ids = {job.printer_id for job in jobs if job.printer_id != printer.id}
    names: dict = {}
    if other_ids:
        result = await db.execute(select(Printer.id, Printer.name).where(Printer.id.in_(other_ids)))
        names = dict(result.all())
    return [
        HeldJobOut.model_validate(job).model_copy(
            update={"printer_name": names.get(job.printer_id)}
        )
        for job in jobs
    ]


@router.post("/{token}/jobs", response_model=HeldJobsOut)
async def list_held_jobs(
    token: str, payload: ReleasePinRequest, db: AsyncSession = Depends(get_db)
):
    printer = await _get_printer_by_token(db, token)
    user = await _resolve_pin(db, token, payload.pin)
    result = await db.execute(
        select(Job)
        .where(
            Job.submitted_by == user.email,
            Job.status == "held",
            _releasable_here(printer),
        )
        .order_by(Job.created_at)
    )
    jobs = await _attach_printer_names(db, printer, result.scalars().all())
    # GoogleWorkspaceUser.name is Google's own full name for the account
    # ("Jessica Dobrzenski"), so there is nothing to assemble from parts
    # here. None for an account with no name set, which the kiosk renders
    # as no name rather than as an empty one.
    return HeldJobsOut(person_name=user.name, jobs=jobs)


@router.post("/{token}/jobs/{job_id}/release", response_model=HeldJobOut)
async def release_job(
    token: str, job_id: UUID, payload: ReleasePinRequest, db: AsyncSession = Depends(get_db)
):
    printer = await _get_printer_by_token(db, token)
    user = await _resolve_pin(db, token, payload.pin)

    job = await db.get(Job, job_id)
    if job is None or job.submitted_by != user.email or job.status != "held":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Held job not found.")
    releasable_here = (job.hold_reason == "pin_release" and job.printer_id == printer.id) or (
        job.hold_reason == "follow_me" and printer.follow_me_enabled
    )
    if not releasable_here:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Held job not found.")

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
    # job's own backend invocation can.
    job.status = "forwarded"
    job.completed_at = datetime.now(UTC)
    if job.held_file_path:
        Path(job.held_file_path).unlink(missing_ok=True)
    job.held_file_path = None
    await db.commit()
    await db.refresh(job)
    return job
