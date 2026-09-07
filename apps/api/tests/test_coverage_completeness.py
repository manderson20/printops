"""Saying how much of a total was actually measured.

A coverage-derived cost covers only the jobs that could be measured. Copies
never can be — walk-up copying produces no document — and a print job whose
spool file aged out before the loop reached it cannot be either. Presenting
such a figure as though it covered everything is the failure this guards
against, and it is invisible: the number looks the same either way.
"""

import inspect

import pytest

from app.reports.coverage_summary import CoverageCompleteness


def test_nothing_measured_is_not_the_same_as_measuring_zero():
    """A period with no measurements has no coverage figure to give. Reporting
    0.0 as though it were an observation would say "these jobs used no ink"."""
    nothing = CoverageCompleteness(measured_jobs=0, unmeasured_jobs=0)
    assert nothing.fraction == 0.0
    assert nothing.is_representative is False


def test_a_mostly_measured_period_is_worth_a_headline():
    assert CoverageCompleteness(measured_jobs=90, unmeasured_jobs=10).is_representative


@pytest.mark.parametrize(
    ("measured", "unmeasured", "because"),
    [
        (1, 99, "a one percent sample cannot stand for the whole"),
        (1, 2, "a third measured is still a sample"),
        (2, 1, "exactly two thirds is the line, and it is included"),
    ],
)
def test_where_the_line_falls(measured, unmeasured, because):
    result = CoverageCompleteness(measured_jobs=measured, unmeasured_jobs=unmeasured)
    assert result.is_representative == (measured / (measured + unmeasured) >= 2 / 3), because


def test_the_unmeasured_remainder_is_always_visible():
    """The count is carried, not just the fraction, because "12 of 400 jobs"
    tells a reader something "3%" does not: how much printing is missing from
    the figure they are looking at."""
    result = CoverageCompleteness(measured_jobs=12, unmeasured_jobs=388)
    assert result.total_jobs == 400
    assert result.unmeasured_jobs == 388
    assert result.fraction == pytest.approx(0.03)
    assert result.is_representative is False


# --- the loop must not starve its own backlog -------------------------------


def test_the_oldest_unmeasured_jobs_are_taken_first():
    """Ordering decides whether a busy day gets measured at all.

    The batch is bounded, so once more than a batch arrives between cycles the
    order determines which jobs are reached. Newest-first takes the latest
    batch every time and never returns to the older ones, which age past the
    spool horizon unmeasured — and it fails hardest during the busiest periods,
    which are the ones most worth measuring.

    Asserted against the query itself because the failure has no symptom: the
    jobs simply stop appearing, and nothing reports a job that was never
    measured as a problem.
    """
    import app.coverage.worker as worker

    source = inspect.getsource(worker.unmeasured_jobs)
    assert "created_at.asc()" in source, "oldest first, nearest expiry first"
    assert "created_at.desc()" not in source, "newest-first starves the backlog"
