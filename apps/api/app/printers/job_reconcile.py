"""Resolves job records that were left mid-delivery and never finished.

A `jobs` row is created by the CUPS backend script the moment delivery starts
(app/routers/jobs.py:create_job) and only leaves status="forwarding" when that
same script reports back. Every way the script can die without reporting
leaves a row that says a job is still printing, forever:

- cupsd sends the backend SIGTERM whenever it cancels, holds or restarts a
  job — including its own "Job restarted because the printer was modified"
  path, which any `lpadmin` against the queue triggers, and its 10800-second
  stuck-job timeout. The backend now reports a cancellation on its way out
  (infra/cups/backends/printops), but only for signals it can catch: SIGKILL,
  a crash, or a machine that loses power leave nothing behind.
- The final status PATCH is best-effort by design — the alternative is failing
  a print that already succeeded because the API was restarting. An API
  restart mid-job therefore strands the row.

The rows are not merely untidy. They read as "printing" on the Jobs page long
after the printer went home for the night, they are excluded from every report
(app/reports/aggregation.py:TERMINAL_STATUSES), and a restart-orphaned row
undercounts its user's actual printing — the reprint that followed it is a
different row. On this server they accumulated at 2-3% of all jobs, unnoticed,
from July until they were counted in August: 107 rows, the oldest six weeks
old.

The same shape as the three faults before it (see docs/ and
app/printers/queue_recovery.py): everything PrintOps collects describes the
device, the device is fine, and the break is on the server side of the path.
What is asked here is cupsd's own job record — the only witness to what became
of a job PrintOps stopped watching.

**Nothing is written off on a guess.** A job cupsd still has in flight is left
alone however old the row is; a 166 MB raster at graphic arts is ordinary
traffic and takes as long as it takes. A job cupsd has no record of is left
alone until the row is old enough that no delivery could still be running, and
even then it is recorded as cancelled with the uncertainty stated in the
message rather than as a failure that was never observed.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer
from app.printers import job_control
from app.printers.capabilities import recorded_color_mode

logger = logging.getLogger(__name__)

# How long a row must have said "forwarding" before it is even asked about.
# Longer than any plausible round trip through the backend script, so an
# ordinary in-progress job is never a candidate — the CUPS lookup would say
# "in flight" anyway, but not asking is cheaper and leaves no room for a race
# between the sweep and a job finishing normally.
STUCK_AFTER = timedelta(minutes=30)

# How long before a row cupsd has no record of is written off. cupsd rolls
# completed jobs out of its history by count (MaxJobs, 500 by default), not by
# age, so waiting longer does not make the answer better — this is only a
# margin for the cases where the record is missing for a passing reason: cupsd
# restarting, or a queue rebuilt underneath a running job.
UNRESOLVABLE_AFTER = timedelta(hours=2)

# Rows resolved per sweep. The first sweep after this shipped had six weeks of
# backlog to work through and each row costs one ipptool round trip to the
# local cupsd; capping it keeps that from turning into a burst of hundreds of
# requests at startup, and the next sweep picks up where this one left off.
MAX_PER_SWEEP = 200


@dataclass
class ReconcileResult:
    """What one sweep did, for the log line and for tests."""

    resolved: int = 0
    left_in_flight: int = 0
    unresolvable_kept: int = 0
    unasked: int = 0


def _superseded_message(cups_job_id: int) -> str:
    return (
        f"Superseded — the print server restarted CUPS job {cups_job_id}, and the later "
        "record of it has what was printed."
    )


UNKNOWN_MESSAGE = (
    "Outcome unknown — delivery stopped without reporting, and the print server no longer "
    "has a record of this job. It may well have printed."
)


async def _superseding_job_ids(db: AsyncSession, candidates: list[Job]) -> set:
    """Which of these rows were superseded by a later attempt at the same CUPS
    job, mapped to the row that superseded them.

    When cupsd restarts a job it runs the backend again, and the backend
    creates a *new* row — so one CUPS job can leave a trail of rows of which
    only the last one saw the delivery through. The earlier ones must not be
    resolved from cupsd's record of that job: they would each take a copy of
    the same page count, and one print would be counted three times in
    everyone's totals.

    Asked as one query over the candidates' (printer, cups job id) pairs
    rather than per row — a sweep with a backlog behind it would otherwise be
    hundreds of round trips before it even reaches cupsd.
    """
    pairs = {(job.printer_id, job.cups_job_id) for job in candidates if job.cups_job_id is not None}
    if not pairs:
        return set()

    result = await db.execute(
        select(Job.id, Job.printer_id, Job.cups_job_id, Job.created_at).where(
            Job.printer_id.in_({printer_id for printer_id, _ in pairs}),
            Job.cups_job_id.in_({cups_job_id for _, cups_job_id in pairs}),
        )
    )
    latest: dict[tuple, tuple] = {}
    for row in result.all():
        key = (row.printer_id, row.cups_job_id)
        if key not in pairs:
            # in_() over two columns independently matches pairs that don't
            # exist together; only the real ones count.
            continue
        best = latest.get(key)
        if best is None or row.created_at > best[1]:
            latest[key] = (row.id, row.created_at)

    superseded = {}
    for job in candidates:
        key = (job.printer_id, job.cups_job_id)
        winner = latest.get(key)
        if winner is not None and winner[0] != job.id:
            superseded[job.id] = winner[0]
    return superseded


def _resolve(
    job: Job,
    outcome: job_control.CupsJobOutcome,
    now: datetime,
    capabilities: dict | None = None,
) -> bool:
    """Applies cupsd's verdict to one row. Returns whether it was resolved."""
    if outcome.in_flight:
        return False

    if outcome.state == job_control.JOB_STATE_COMPLETED:
        job.status = "forwarded"
        job.error_message = None
        # Taken now rather than left null: this is the same attribute the
        # backend script would have reported, read from the same record, and
        # without it the job is in the reports with no pages against it.
        job.page_count = outcome.page_count
        job.color_mode = recorded_color_mode(capabilities, outcome.color_mode)
        job.duplex = outcome.duplex
        job.paper_size = outcome.paper_size
    elif outcome.state == job_control.JOB_STATE_ABORTED:
        job.status = "failed"
        detail = f" CUPS's reason: {outcome.reason}." if outcome.reason else ""
        job.error_message = f"Aborted at the print server.{detail}"
    elif outcome.state == job_control.JOB_STATE_CANCELED:
        job.status = "cancelled"
        job.error_message = (
            "Cancelled at the print server — CUPS ends a job's delivery this way when it "
            "is cancelled, held or restarted."
        )
    elif outcome.state is None:
        if now - job.created_at < UNRESOLVABLE_AFTER:
            return False
        # Not "failed": nothing failed as far as anyone observed, and a
        # failure count is the one number an admin acts on. Cancelled with the
        # doubt spelled out is the honest place to put a job whose fate is
        # genuinely unknown.
        job.status = "cancelled"
        job.error_message = UNKNOWN_MESSAGE
    else:
        return False

    job.completed_at = now
    return True


async def reconcile_stuck_jobs(
    db: AsyncSession, now: datetime | None = None, limit: int = MAX_PER_SWEEP
) -> ReconcileResult:
    """Resolves job rows left saying "forwarding" by a backend that never
    reported back. Returns what it did.

    Deliberately tolerant of a cupsd that won't answer: a row it can't get an
    answer about is left exactly as it is for the next sweep. Being wrong here
    is worse than being late — these rows are what a user's printing history
    and their quota are counted from.
    """
    now = now or datetime.now(UTC)
    result = ReconcileResult()

    candidates = (
        (
            await db.execute(
                select(Job)
                .where(Job.status == "forwarding", Job.created_at < now - STUCK_AFTER)
                .order_by(Job.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return result

    superseded = await _superseding_job_ids(db, list(candidates))
    # One lookup for the whole batch: _resolve needs to know whether each
    # job's printer can print color at all before believing what CUPS says
    # the job's color mode was (see recorded_color_mode).
    capabilities_by_printer = {
        row.id: row.capabilities
        for row in (
            await db.execute(
                select(Printer.id, Printer.capabilities).where(
                    Printer.id.in_({job.printer_id for job in candidates})
                )
            )
        ).all()
    }

    for job in candidates:
        if job.id in superseded:
            job.status = "cancelled"
            # Marked as well as cancelled: the reports count rows, so an
            # earlier attempt left in them reports one job as several
            # (app/reports/aggregation.py:_apply_filters).
            job.superseded_by_job_id = superseded[job.id]
            job.error_message = _superseded_message(job.cups_job_id)
            job.completed_at = now
            result.resolved += 1
            continue

        if job.cups_job_id is None:
            # Nothing to ask about — the row was created before the backend
            # had a CUPS job id to record, which no current code path does.
            outcome = job_control.CupsJobOutcome(state=None)
        else:
            outcome = await _run_lookup(str(job.printer_id), job.cups_job_id)

        if outcome is None:
            result.unasked += 1
            continue
        if _resolve(job, outcome, now, capabilities_by_printer.get(job.printer_id)):
            result.resolved += 1
        elif outcome.state is None:
            result.unresolvable_kept += 1
        else:
            result.left_in_flight += 1

    if result.resolved:
        await db.commit()
        logger.info(
            "Reconciled %d job record(s) left mid-delivery (%d still in flight, "
            "%d not yet old enough to write off, %d cupsd wouldn't answer for).",
            result.resolved,
            result.left_in_flight,
            result.unresolvable_kept,
            result.unasked,
        )
    return result


async def _run_lookup(printer_id: str, cups_job_id: int) -> job_control.CupsJobOutcome | None:
    """One ipptool round trip, off the event loop — same treatment every other
    subprocess call in app/printers/ gets."""
    return await asyncio.to_thread(job_control.cups_job_outcome, printer_id, cups_job_id)
