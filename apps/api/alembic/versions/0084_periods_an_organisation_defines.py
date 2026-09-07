"""periods an organisation defines

Revision ID: 0084
Revises: 0083
Create Date: 2026-09-07 20:00:00.000000

"""

import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: Union[str, Sequence[str], None] = "0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    0083 made the year's *boundaries* configurable. The vocabulary was still
    compiled in: four period names, one of them "semester". For an installation
    that is not a school — and this is a project other organisations install —
    "This semester" is simply the wrong word, and there was no way to change it.

    A school year and a fiscal year are the same object: a year that need not
    begin in January, divided into named parts. So there is no organisation type
    in this schema and none in the code that reads it. A school sets the noun to
    "School year" and names its terms Fall and Spring; a business sets "FY" and
    names them Q1 to Q4; nothing downstream can tell them apart. Stated here
    because the alternative arrives by increments — one flag for schools, then a
    path only schools take, then a bug only businesses see.

    Terms subdivide the *reporting* year, not the calendar year. Q1 of a
    July-start year is July to September. The calendar year remains available
    everywhere as its own period, because a budget question and a calendar-year
    comparison are different questions and an organisation may want both.

    **Nothing moves for an existing installation.** The calendar is seeded from
    the boundaries 0083 already stored, and the two semesters are recreated from
    them, so every report resolves to the same dates the day after this runs as
    the day before.
    """
    op.create_table(
        "reporting_calendars",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("year_start_month", sa.Integer(), server_default="7", nullable=False),
        sa.Column("year_start_day", sa.Integer(), server_default="1", nullable=False),
        sa.Column("year_noun", sa.String(), server_default="Year", nullable=False),
        sa.Column("label_style", sa.String(), server_default="auto", nullable=False),
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

    op.create_table(
        "reporting_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_month", sa.Integer(), nullable=False),
        sa.Column("start_day", sa.Integer(), server_default="1", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
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

    op.create_table(
        "reporting_term_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reporting_year", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
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
    op.create_index(
        "ix_reporting_term_instances_reporting_year",
        "reporting_term_instances",
        ["reporting_year"],
    )

    _seed()


def _seed() -> None:
    """Carry the existing calendar forward, if there is one.

    A `report_formula_settings` row exists only once this database has served a
    report, which means it has been showing people periods called "school year"
    and "semester". Those reports must not change meaning on upgrade, so the
    vocabulary that was implied by the code is written down explicitly.

    A database without that row has never shown anybody a period. It gets the
    neutral default — "Year", no terms — rather than one district's school
    calendar, which is the whole point of the change.
    """
    connection = op.get_bind()
    now = datetime.now(UTC)
    existing = connection.execute(
        sa.text(
            "SELECT school_year_start_month, school_year_start_day, "
            "spring_semester_start_month, spring_semester_start_day "
            "FROM report_formula_settings LIMIT 1"
        )
    ).first()

    if existing is None:
        connection.execute(
            sa.text(
                "INSERT INTO reporting_calendars "
                "(id, year_start_month, year_start_day, year_noun, label_style, "
                "created_at, updated_at) "
                "VALUES (:id, 7, 1, 'Year', 'auto', :now, :now)"
            ).bindparams(id=uuid.uuid4(), now=now),
        )
        return

    year_month, year_day, spring_month, spring_day = existing

    connection.execute(
        sa.text(
            "INSERT INTO reporting_calendars "
            "(id, year_start_month, year_start_day, year_noun, label_style, "
            "created_at, updated_at) "
            "VALUES (:id, :month, :day, 'School year', 'auto', :now, :now)"
        ).bindparams(id=uuid.uuid4(), month=year_month, day=year_day, now=now),
    )

    # Fall began where the school year began and ran until Spring — which is
    # exactly what the retired two-branch `_semester_start` computed.
    for position, (name, month, day) in enumerate(
        [
            ("Fall Semester", year_month, year_day),
            ("Spring Semester", spring_month, spring_day),
        ]
    ):
        connection.execute(
            sa.text(
                "INSERT INTO reporting_terms "
                "(id, name, start_month, start_day, position, created_at, updated_at) "
                "VALUES (:id, :name, :month, :day, :position, :now, :now)"
            ).bindparams(
                id=uuid.uuid4(), name=name, month=month, day=day, position=position, now=now
            ),
        )


def downgrade() -> None:
    """Downgrade schema.

    The configured vocabulary is lost, which is the honest outcome: without
    these tables the code has nowhere to read it from and falls back to the
    boundaries in `report_formula_settings`, which 0083 still holds.
    """
    op.drop_index(
        "ix_reporting_term_instances_reporting_year", table_name="reporting_term_instances"
    )
    op.drop_table("reporting_term_instances")
    op.drop_table("reporting_terms")
    op.drop_table("reporting_calendars")
