"""Measuring finished jobs, off the print path.

Deliberately not in the CUPS backend. The backend runs while somebody is
waiting for their document, and rendering a long report there would delay the
print — or, worse, fail it. Coverage is worth having and worth having late; it
is never worth a job not printing.

The loop claims work by inserting a row per job. The unique index on job_id is
what stops two cycles measuring the same job, rather than a lock or a flag that
has to be cleaned up after a crash.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coverage.inkcov import measure
from app.coverage.spool import document_paths, plausible_for
from app.models.job import Job
from app.models.job_coverage import JobCoverage

logger = logging.getLogger(__name__)

# Jobs younger than this are left alone: CUPS may still be writing, and a
# partially spooled file measures as a short document rather than failing.
SETTLE = timedelta(minutes=5)

# How far back to look. CUPS keeps job data for PreserveJobFiles — three days
# by default — so anything older has no file to measure and asking again each
# cycle is wasted work. Jobs that age out are recorded as "expired" rather than
# left unmeasured, so a gap in the data is visible as a gap.
HORIZON = timedelta(days=3)

# Per cycle. Bounded because one long backlog should not hold the loop for
# minutes; the next cycle takes the next batch.
BATCH = 25


async def unmeasured_jobs(db: AsyncSession, *, now: datetime, limit: int = BATCH) -> list[Job]:
    """Finished print jobs inside the spool window with no coverage row yet."""
    measured = select(JobCoverage.job_id)
    stmt = (
        select(Job)
        .where(
            Job.created_at <= now - SETTLE,
            Job.created_at >= now - HORIZON,
            Job.cups_job_id.is_not(None),
            Job.id.not_in(measured),
        )
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def measure_job(db: AsyncSession, job: Job) -> str:
    """Measure one job and record the outcome. Returns the state recorded.

    Every path writes a row. A job that could not be measured is a fact worth
    keeping — it stops the loop retrying it forever, and it lets a report say
    how much of a total was actually measured rather than implying all of it
    was.
    """
    state, coverage, detail = "failed", None, None

    paths = document_paths(job.cups_job_id) if job.cups_job_id is not None else []
    if not paths:
        state, detail = "expired", "no spool file for this job id"
    elif not plausible_for(paths[0], job.created_at):
        # The id was reused by a later spool. Refusing the match loses a
        # measurement; taking it would attribute somebody's document, and its
        # cost, to the wrong person.
        state, detail = "expired", "spool file does not match this job's time"
    else:
        totals = [0.0, 0.0, 0.0, 0.0]
        pages = 0
        for path in paths:
            measured, why = await measure(path)
            if measured is None:
                detail = why
                continue
            pages += measured.pages
            totals = [
                total + value * measured.pages
                for total, value in zip(
                    totals,
                    (measured.cyan, measured.magenta, measured.yellow, measured.black),
                )
            ]
        if pages:
            state, coverage, detail = "measured", [t / pages for t in totals], None
        elif detail and "not a PDF" in detail:
            state = "unsupported"

    row = JobCoverage(
        job_id=job.id,
        state=state,
        pages_measured=0 if coverage is None else pages,
        cyan=coverage[0] if coverage else 0.0,
        magenta=coverage[1] if coverage else 0.0,
        yellow=coverage[2] if coverage else 0.0,
        black=coverage[3] if coverage else 0.0,
        measured_at=datetime.now(UTC),
        detail=detail,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # Another cycle got there first. The unique index is the claim, so
        # losing the race is the normal way for a duplicate to be prevented.
        await db.rollback()
        return "duplicate"
    return state
