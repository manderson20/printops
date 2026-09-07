import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Why a measurement might not exist. Recorded rather than left null, because
# "no coverage" and "coverage of zero" are different facts and a report that
# cannot tell them apart will average one into the other.
COVERAGE_STATES = (
    "measured",
    # The spool file was gone by the time the loop reached it. CUPS keeps job
    # data for PreserveJobFiles and no longer; an outage longer than that means
    # those jobs are never measurable, which is a gap to state rather than a
    # zero to invent.
    "expired",
    # Not a PDF. Some clients send PostScript or a device language directly,
    # and the renderer cannot report coverage for those.
    "unsupported",
    # Ghostscript failed, or the file was damaged.
    "failed",
    # A copy, not a print: walk-up copier use never passes through the print
    # server, so no document exists to measure. Never blended into a
    # coverage-based figure without saying so.
    "no_document",
)


class JobCoverage(Base, TimestampMixin):
    """How much ink one job's pages would actually lay down.

    Measured from the spooled PDF with Ghostscript's inkcov device, which
    reports the fraction of each page covered by each colorant. It is what the
    *renderer* would deposit rather than what the device does — halftoning,
    toner-save and the printer's own colour management all differ — so it is a
    strong relative measure and an approximate absolute one.

    Worth having because page counts throw the difference away entirely. A real
    four-page job measured on this estate ran 11%, 8.6%, 9.8% and 1.3% black:
    an eight-fold spread inside one document, all four counted identically by
    every report PrintOps had before this.
    """

    __tablename__ = "job_coverage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # The job this describes. Not a foreign key with a cascade: coverage is an
    # observation about a job rather than part of it. The failed-job purge in
    # app/main.py deletes these alongside the jobs it removes — which it did
    # not do when this comment first claimed it, leaving orphans that nothing
    # could reach. A comment describing behaviour that does not exist is worse
    # than no comment, because it stops the next reader looking.
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, unique=True)

    state: Mapped[str] = mapped_column(String, default="measured", server_default="measured")

    # Pages actually rendered and measured. May be fewer than the job's page
    # count if the file was truncated; the ratio is what makes that visible
    # rather than a silently low average.
    pages_measured: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Mean fraction of a page covered by each colorant, 0..1, averaged over the
    # pages measured. Stored per channel because that is what inkcov reports
    # and what cartridge yields are quoted against — a single "total ink"
    # number cannot be compared with a per-cartridge yield without assuming a
    # colour mix.
    cyan: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    magenta: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    yellow: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    black: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Kept for the failures worth chasing — a renderer error, a file that was
    # not what its name claimed.
    detail: Mapped[str | None] = mapped_column(String, default=None)
