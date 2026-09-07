import uuid
from datetime import date

from sqlalchemy import Date, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# How an instance is named. "auto" derives from the start month, which is right
# almost always; the override exists because a fiscal year starting in October
# is FY2027 to some organisations and FY2026 to others, and guessing wrong puts
# the wrong number on every report.
LABEL_STYLES = ("auto", "spanning", "single")


class ReportingCalendar(Base, TimestampMixin):
    """When this organisation's year starts, and what it calls it.

    A school year and a fiscal year are the same thing: a year that does not
    begin in January, divided into named parts. The difference is entirely the
    noun. So there is no organisation type here and none anywhere else — no
    `is_school`, no branch that behaves differently for one kind of installer.
    A school sets `year_noun` to "School year" and names its terms Fall and
    Spring; a business sets "Fiscal year" and names them Q1 to Q4; nothing
    downstream can tell them apart.

    Worth stating because the alternative arrives by increments: one flag for
    schools, then a path only schools take, then a bug only businesses see.
    """

    __tablename__ = "reporting_calendars"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # January, not July. A July start is a school's — and a fiscal year's, and
    # neither is a safe guess for an organisation that has not said. The
    # calendar year is the only year every installation certainly has, so it is
    # what a fresh install reports against until somebody configures otherwise.
    # This defaulted to 7 briefly in 0.81.0: one district's calendar handed to
    # every installation, which is the exact class of thing this table exists
    # to remove.
    year_start_month: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    year_start_day: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # What to call a year of this organisation's, shown wherever a period is
    # named. "Year" is the neutral default; this district's is "School year".
    year_noun: Mapped[str] = mapped_column(String, default="Year", server_default="Year")

    label_style: Mapped[str] = mapped_column(String, default="auto", server_default="auto")


class ReportingTerm(Base, TimestampMixin):
    """One part of the year, as a repeating pattern.

    Two rows make semesters, three make trimesters, four make quarters, and
    none at all is an organisation that does not subdivide its year — which is
    a supported shape, not an unconfigured one. Every past and future year is
    generated from these, so nobody has to add rows each August, and a year
    from before PrintOps was installed still has terms to report against.
    """

    __tablename__ = "reporting_terms"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String)
    start_month: Mapped[int] = mapped_column(Integer)
    start_day: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    # Ties an instance to its counterpart a year earlier, which is what makes
    # "compared with the same term last year" answerable. Ordering within the
    # year comes from the dates, not from this.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ReportingTermInstance(Base, TimestampMixin):
    """A term of one particular year, with real dates.

    For the year whose dates genuinely differed. Instances for a reporting year
    win *entirely* over the generated pattern for that year — an admin
    overriding one term is asked to state that year's terms, rather than
    leaving a year half-generated and half-overridden that nobody can reason
    about afterwards.

    `reporting_year` is the year the reporting year *starts* in, so a July-start
    2026 row belongs to the year running July 2026 to June 2027 whatever month
    the term itself falls in.
    """

    __tablename__ = "reporting_term_instances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    reporting_year: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String)
    start_date: Mapped[date] = mapped_column(Date)
    # Inclusive: the last day the term covers, which is how a school publishes
    # its calendar. Converted to an exclusive bound once, at resolution.
    end_date: Mapped[date] = mapped_column(Date)

    # Which pattern term this stands in for, where there is one. Null for a term
    # that existed only that year.
    position: Mapped[int | None] = mapped_column(Integer, default=None)
