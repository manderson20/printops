"""the district is not the product

Revision ID: 0083
Revises: 0082
Create Date: 2026-09-07 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: Union[str, Sequence[str], None] = "0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    PrintOps is installed by other organisations, and three facts about the
    first district were compiled into the source where every one of them would
    inherit it.

    `student_count` replaces a constant of 930 — one district's roll — which
    drove "enough to hand every student N sheets" on every screen showing that
    fact. It defaults to **0**, not to a number: the fact is omitted entirely
    until somebody says what the enrolment is. A missing fact is better than a
    confident wrong one, and 930 was confidently wrong everywhere else.

    The school-year and spring-semester boundaries were likewise constants.
    1 July and 1 January are reasonable defaults rather than one district's
    policy, so they are seeded as such — but they decide the default period
    every Insights screen opens on, which makes them a calendar somebody should
    be able to set.

    Nothing changes for an existing installation on upgrade. The calendar
    defaults are the values that were compiled in; `student_count` starts at 0,
    which means the district that has been seeing the per-student fact stops
    seeing it until an admin fills the figure in on Settings > Insights. That
    is the intended trade: the number it was showing was a placeholder nobody
    had confirmed, and its own comment said so.
    """
    op.add_column(
        "report_formula_settings",
        sa.Column("student_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "report_formula_settings",
        sa.Column("school_year_start_month", sa.Integer(), server_default="7", nullable=False),
    )
    op.add_column(
        "report_formula_settings",
        sa.Column("school_year_start_day", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "report_formula_settings",
        sa.Column("spring_semester_start_month", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "report_formula_settings",
        sa.Column("spring_semester_start_day", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema.

    The compiled-in constants are gone from the source, so a downgrade past
    this leaves the calendar at its defaults and the per-student fact absent —
    which is what the code without these columns now does anyway.
    """
    op.drop_column("report_formula_settings", "spring_semester_start_day")
    op.drop_column("report_formula_settings", "spring_semester_start_month")
    op.drop_column("report_formula_settings", "school_year_start_day")
    op.drop_column("report_formula_settings", "school_year_start_month")
    op.drop_column("report_formula_settings", "student_count")
