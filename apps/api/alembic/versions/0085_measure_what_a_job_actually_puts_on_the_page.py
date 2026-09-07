"""measure what a job actually puts on the page

Revision ID: 0085
Revises: 0084
Create Date: 2026-09-07 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: Union[str, Sequence[str], None] = "0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Every cost PrintOps reports is a page count multiplied by a rate. That
    treats a page of dense graphics and a page with one line on it as the same
    thing, and they are not: a real four-page job on this estate measured 11%,
    8.6%, 9.8% and 1.3% black — an eight-fold spread inside one document.

    Ghostscript can report how much of a page each colorant covers, and the
    spooled document is a PDF that CUPS keeps for a few days after printing.
    That is enough to measure a job after it has printed, off the print path,
    where the measurement can never delay or break somebody's printing.

    This adds somewhere to keep the result, per job and per colorant. Per
    colorant because that is what a cartridge yield is quoted against; a single
    "total ink" figure cannot be priced without assuming a colour mix nobody
    measured.

    `iso_coverage_per_channel` is the baseline every coverage-derived cost is a
    ratio against. It is a setting rather than a constant: 0.05 is the ISO/IEC
    19752 and 19798 test page, but vendors deviate and an installer may hold
    datasheets quoting something else. Since it multiplies every derived
    figure, assuming it would be a way to be confidently wrong about money.

    Nothing existing changes. No cost that PrintOps reports today is computed
    differently after this runs; the rated-yield figure stays exactly as it was
    and remains the answer wherever a job was never measured — which includes
    every copy, since walk-up copier use never produces a document to measure.
    """
    op.create_table(
        "job_coverage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(), server_default="measured", nullable=False),
        sa.Column("pages_measured", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cyan", sa.Float(), server_default="0", nullable=False),
        sa.Column("magenta", sa.Float(), server_default="0", nullable=False),
        sa.Column("yellow", sa.Float(), server_default="0", nullable=False),
        sa.Column("black", sa.Float(), server_default="0", nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Unique: a job is measured once. The loop claims work by inserting, so the
    # constraint is also what stops two cycles measuring the same job twice.
    op.create_index("ix_job_coverage_job_id", "job_coverage", ["job_id"], unique=True)

    op.add_column(
        "report_formula_settings",
        sa.Column(
            "iso_coverage_per_channel",
            sa.Float(),
            server_default="0.05",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    The measurements are lost. Costs fall back to the rated-yield figure, which
    is what every report used before this and what they still use for anything
    unmeasured.
    """
    op.drop_column("report_formula_settings", "iso_coverage_per_channel")
    op.drop_index("ix_job_coverage_job_id", table_name="job_coverage")
    op.drop_table("job_coverage")
