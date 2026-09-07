"""What coverage measurement can and cannot say about a set of jobs.

The number is easy; the honesty is the hard part. Coverage exists for print
jobs and for nothing else — walk-up copying never passes through the print
server, so no document exists to measure — and within printing, a job whose
spool file aged out before the loop reached it is unmeasured too.

So any coverage-derived total covers a *subset*, and presenting it as though it
covered everything would be a confident overstatement of how much is known. A
summary therefore carries its own coverage of the data alongside the figure,
and callers are expected to show both.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_coverage import JobCoverage


@dataclass(frozen=True)
class CoverageCompleteness:
    """How much of a period's printing was actually measured."""

    measured_jobs: int
    unmeasured_jobs: int

    @property
    def total_jobs(self) -> int:
        return self.measured_jobs + self.unmeasured_jobs

    @property
    def fraction(self) -> float:
        """0.0 to 1.0. Zero when nothing was measured, which is distinct from
        "measured, and it was all blank"."""
        if self.total_jobs == 0:
            return 0.0
        return self.measured_jobs / self.total_jobs

    @property
    def is_representative(self) -> bool:
        """Whether a coverage-derived figure is worth showing as a headline.

        Two thirds is a judgement, not a law, and it is here rather than in a
        template so it is one decision rather than one per screen. Below it the
        figure is still true of what it measured and still worth showing — but
        as a sample, not as the answer, because the unmeasured remainder could
        move the total in either direction by more than the figure itself.
        """
        return self.total_jobs > 0 and self.fraction >= 2 / 3


async def completeness(db: AsyncSession, job_ids: list) -> CoverageCompleteness:
    """How many of these jobs carry a usable measurement.

    A row exists for every job the loop has considered, including the ones it
    could not measure, so "no row yet" and "tried and failed" are counted the
    same way here — both are jobs whose ink is unknown, which is what a reader
    needs to know.
    """
    if not job_ids:
        return CoverageCompleteness(measured_jobs=0, unmeasured_jobs=0)

    measured = (
        await db.execute(
            select(func.count())
            .select_from(JobCoverage)
            .where(JobCoverage.job_id.in_(job_ids), JobCoverage.state == "measured")
        )
    ).scalar_one()

    return CoverageCompleteness(
        measured_jobs=measured,
        unmeasured_jobs=len(job_ids) - measured,
    )
