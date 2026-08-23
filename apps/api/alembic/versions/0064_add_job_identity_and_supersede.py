"""record CUPS's own job uuid, and mark rows superseded by a later attempt

Revision ID: 0064
Revises: 0063
Create Date: 2026-08-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: Union[str, Sequence[str], None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Two columns for the same problem: cupsd re-runs the backend for every retry
    of a job, the backend registers a row each time, and one job then appears
    in the reports as many.

    `cups_job_uuid` is CUPS's own permanent identifier for a job
    (job-uuid=urn:uuid:..., which the backend already receives and now keeps).
    `cups_job_id` cannot do this work: job numbers are per-spool and reset when
    the spool is cleared, so an unrelated job inherits an old number and, if
    retries were matched on it, would rewrite a stranger's history as its own
    earlier attempt.

    `superseded_by_job_id` points at the row that carries the outcome. Marking
    the earlier attempts cancelled is not enough on its own: every report
    counts rows, so 51 attempts at one job became 50 cancelled jobs plus one
    rather than the single job a teacher actually sent. The reports exclude
    rows with this set, which is what makes the count mean what it says, and
    the whole trail stays on the Jobs page where it is history rather than
    arithmetic.
    """
    op.add_column("jobs", sa.Column("cups_job_uuid", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("superseded_by_job_id", sa.Uuid(), nullable=True))
    op.create_index("ix_jobs_cups_job_uuid", "jobs", ["cups_job_uuid"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_jobs_cups_job_uuid", table_name="jobs")
    op.drop_column("jobs", "superseded_by_job_id")
    op.drop_column("jobs", "cups_job_uuid")
