"""Notices pages PrintOps handed to a printer that the printer never printed.

The case this was written for: on 2026-09-04 a teacher reported that a ten-page
PDF "didn't print". Every record said it had. CUPS forwarded it, the backend
script reported success, and the printer's own job history had it as
`job-completed-successfully` — while `job-media-sheets-completed` sat at 0 and
the device's page counter did not move by a single page across it. An HP
LaserJet M607 had accepted a PDF its firmware could not render and closed the
job as a success. It had done the same thing to the same document twice the day
before; she worked around it by printing from Google Docs instead, and it
reached an admin a day later only because she happened to mention it.

Nothing PrintOps collects about a job would ever have shown this. Every layer
in the path is reporting the truth as it knows it — the job really was
forwarded, and the printer really did say it finished. The only witness that
nothing came out is the page counter, which this server has been recording
every thirty minutes since counter history shipped and has never once compared
against what it sent.

So compare them. For each pair of consecutive counter readings, add up the
pages of the jobs forwarded in between and see how far the counter actually
moved. This deliberately cannot speak about a single job — the readings are
half an hour apart — but "these three jobs together lost thirty pages" is the
sentence that gets someone to look, and it is one PrintOps can support with
evidence it already holds.

Run over fourteen days of this fleet's history while it was being written, it
flagged eight windows: the case above, and seven nobody had reported at all —
the largest being 207 pages of a single PDF on a bizhub. That one is very
probably a *different* fault wearing the same signature (Account Track
declining jobs from users it has no account for), which is the argument for
reporting the symptom instead of guessing at the cause. "We sent pages and none
came out" is worth surfacing whatever produced it.

**Deliberately biased towards silence.** Walk-up copying, a technician's test
prints, a report run from the device's own panel — everything that moves the
counter without passing through PrintOps pushes a window towards looking
healthy, and that is the right direction to be wrong in. This is a diagnostic
an admin has to trust before they will act on it, and one false alarm costs
more than several misses. Everything below that looks over-cautious is
over-cautious on purpose, and says so where it isn't obvious.

The functions here are pure apart from `sweep_unprinted_pages`, which owns the
queries and writes the verdict to the printer row. The status poll renders it
from there (app/printers/status.py) rather than recomputing: this is a
half-hourly question asked of two tables, and the poll it would ride on runs
every sixty seconds.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer
from app.models.snmp import PrinterCounterReading

logger = logging.getLogger(__name__)

# The keyword the UI keys off to render this as a warning about output rather
# than a fault the printer is currently in (apps/web/.../printers).
PAGES_NOT_PRINTED_REASON = "printops-pages-not-printed"

# How far back the evidence is gathered. A day, so that a fault which started
# this morning is reported this morning and one fixed yesterday has stopped
# being reported by tomorrow — the same reasoning network_health.WINDOW uses at
# its own scale. Longer would make the number more impressive and the report
# less actionable: an admin needs to know whether this is happening *now*.
WINDOW = timedelta(hours=24)

# Pages a window has to lose before it is worth saying out loud. A page count
# and a counter delta are gathered by completely different means — one from
# CUPS, one from SNMP — and the odd single page between them is ordinary. Five
# is well above that and far below the tens or hundreds a real discard costs.
MIN_SHORTFALL = 5

# ...and the counter must also have moved by less than this fraction of what
# was sent. A window where 200 pages were sent and 190 counted has lost ten
# pages to arithmetic, not to a printer throwing work away; a window where 216
# were sent and 9 counted is a different animal. Both conditions must hold, so
# a big busy window can't trip the alarm on rounding alone.
MAX_DELIVERED_FRACTION = 0.5

# A verdict older than this is not reported. The sweep rewrites the row every
# cycle and writes nothing when a printer is clear, so this only matters when
# the sweep itself stops — SNMP polling turned off, the loop dying, the row
# outliving the fault. A stale warning about pages that went missing at some
# unknown point in the past is exactly the kind an admin learns to scroll past.
STALE_AFTER = timedelta(hours=3)

# Documents named in the message. Enough to recognise the pattern — the same
# file failing over and over is the single most useful thing this can say —
# without turning a status line into a job list.
MAX_DOCUMENTS_NAMED = 3


@dataclass(frozen=True)
class SentJob:
    """One forwarded job, as far as this module needs to know about it."""

    completed_at: datetime
    pages: int
    duplex: bool | None
    document_name: str | None

    @property
    def least_expected(self) -> int:
        """The fewest counter increments this job could honestly have produced.

        Duplex is the reason this isn't just `pages`. Page counters are per
        impression on most devices and per sheet on some, and PrintOps does not
        know which for any given model — so a ten-page duplex job might move a
        counter by ten or by five. Taking the smaller number means a duplex job
        can never *cause* an alarm, only fail to prevent one.
        """
        return ceil(self.pages / 2) if self.duplex else self.pages


@dataclass(frozen=True)
class LostWindow:
    """One pair of counter readings that the jobs between them don't account
    for."""

    started_at: datetime
    ended_at: datetime
    expected: int
    counted: int
    documents: tuple[str, ...]
    jobs: int

    @property
    def shortfall(self) -> int:
        return self.expected - self.counted


@dataclass(frozen=True)
class UnprintedPages:
    """A printer's worth of windows that lost pages, over WINDOW."""

    windows: tuple[LostWindow, ...]

    @property
    def shortfall(self) -> int:
        return sum(window.shortfall for window in self.windows)

    @property
    def jobs(self) -> int:
        return sum(window.jobs for window in self.windows)

    @property
    def since(self) -> datetime:
        return min(window.started_at for window in self.windows)

    @property
    def documents(self) -> tuple[str, ...]:
        """Named worst-window-first, and de-duplicated. One document failing
        repeatedly is the finding; seeing it listed three times is not."""
        seen: dict[str, None] = {}
        for window in sorted(self.windows, key=lambda w: w.shortfall, reverse=True):
            for name in window.documents:
                seen.setdefault(name, None)
        return tuple(seen)


def _window_verdict(
    started_at: datetime,
    ended_at: datetime,
    counted: int,
    jobs: list[SentJob],
) -> LostWindow | None:
    """Judges one pair of readings. None when there is nothing to report."""
    if counted < 0:
        # The counter went backwards: a device reset, a replaced formatter, a
        # counter that rolled. Whatever happened, the delta is not a
        # measurement of this window's printing and nothing can be concluded.
        return None

    expected = sum(job.least_expected for job in jobs)
    if expected <= 0:
        return None
    shortfall = expected - counted
    if shortfall < MIN_SHORTFALL or counted >= expected * MAX_DELIVERED_FRACTION:
        return None

    return LostWindow(
        started_at=started_at,
        ended_at=ended_at,
        expected=expected,
        counted=counted,
        documents=tuple(job.document_name for job in jobs if job.document_name),
        jobs=len(jobs),
    )


def assess(readings: list[tuple[datetime, int]], jobs: list[SentJob]) -> UnprintedPages | None:
    """Compares what was sent against what the counter recorded, one pair of
    consecutive readings at a time.

    `readings` must be ordered by time. A job belongs to the window that ends
    at the first reading taken after it finished — the counter can only have
    seen it by then — so jobs completed after the last reading are held over
    for the next sweep rather than being judged against a counter that has not
    been read since.
    """
    if len(readings) < 2:
        return None

    windows = []
    for (started_at, before), (ended_at, after) in zip(readings, readings[1:]):
        inside = [job for job in jobs if started_at < job.completed_at <= ended_at]
        if not inside:
            continue
        verdict = _window_verdict(started_at, ended_at, after - before, inside)
        if verdict is not None:
            windows.append(verdict)

    return UnprintedPages(windows=tuple(windows)) if windows else None


def unprinted_reason(verdict: UnprintedPages) -> str:
    """The operator-facing explanation for a freshly computed verdict."""
    return _reason(verdict.shortfall, len(verdict.windows), verdict.documents)


def _reason(pages: int, window_count: int, documents: tuple[str, ...]) -> str:
    """Says what was measured, says plainly what it cannot know, and points at
    the one check that settles it — without naming a cause this has no way to
    establish.

    Takes the three numbers it reads rather than a verdict, so that the stored
    record (recorded_reason) and the live one produce the same sentence without
    the stored one having to fabricate a verdict object to get at it.
    """
    page_word = "page" if pages == 1 else "pages"
    where = "one half-hour window" if window_count == 1 else f"{window_count} half-hour windows"

    hours = int(WINDOW.total_seconds() // 3600)
    span = "24 hours" if hours == 24 else f"{hours} hours"

    named = documents[:MAX_DOCUMENTS_NAMED]
    if named:
        listed = ", ".join(f'"{name}"' for name in named)
        more = len(documents) - len(named)
        also = f", and {more} other document{'s' if more > 1 else ''}" if more > 0 else ""
        documents = f" The jobs involved were {listed}{also}."
    else:
        documents = ""

    return (
        f"{pages} {page_word} PrintOps sent to this printer in the last {span} cannot be "
        f"accounted for: across {where} its own page counter moved far less than the jobs "
        f"delivered in that time should have moved it.{documents} A printer that accepts a "
        "job, reports it completed and prints nothing is usually its firmware declining to "
        "render the file rather than a fault you can see on the device — which means the "
        "same document will keep failing while everything else prints normally. Worth "
        "reprinting one of these from a different application to confirm before changing "
        "anything about the queue."
    )


def to_record(verdict: UnprintedPages, now: datetime) -> dict:
    """The verdict as it is stored on the printer row.

    Flattened rather than kept as the whole window list: this is a status
    line's backing data, not an audit trail, and the readings and jobs it was
    computed from are both still in their own tables for anyone working back to
    it. `jobs` and `since` are not read by the message — they are here so that
    a row someone is looking at in the database says what was found and when it
    started, which is the first thing asked of it."""
    return {
        "shortfall": verdict.shortfall,
        "windows": len(verdict.windows),
        "jobs": verdict.jobs,
        "documents": list(verdict.documents),
        "since": verdict.since.isoformat(),
        "assessed_at": now.isoformat(),
    }


def recorded_reason(record: dict | None, now: datetime | None = None) -> str | None:
    """Rebuilds the message from a stored record, or None when there is nothing
    current to say. Tolerant of a malformed row for the same reason
    network_health._prune is: a rolling diagnostic should lose its own entry
    rather than take the status poll down with it."""
    if not record:
        return None
    now = now or datetime.now(UTC)
    try:
        assessed_at = datetime.fromisoformat(record["assessed_at"])
        pages = int(record["shortfall"])
        windows = int(record["windows"])
        documents = tuple(str(name) for name in record.get("documents") or ())
    except (KeyError, TypeError, ValueError):
        return None
    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=UTC)
    if now - assessed_at > STALE_AFTER or pages < MIN_SHORTFALL or windows < 1:
        return None

    # Through the same formatter the live verdict uses, so a message read off
    # the row and one computed a moment ago cannot drift apart.
    return _reason(pages, windows, documents)


@dataclass
class SweepResult:
    """What one sweep did, for the log line and for tests."""

    assessed: int = 0
    flagged: int = 0
    cleared: int = 0


async def sweep_unprinted_pages(db: AsyncSession, now: datetime | None = None) -> SweepResult:
    """Reassesses every active printer and records the verdict on its row.

    Written as three queries and a grouping in Python rather than one clever
    statement per printer: the whole fleet's last day is a few thousand rows,
    and the arithmetic is the part that has to be reviewable.
    """
    now = now or datetime.now(UTC)
    since = now - WINDOW
    result = SweepResult()

    printers = (
        (
            await db.execute(
                select(Printer).where(
                    Printer.archived_at.is_(None),
                    Printer.is_virtual.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    if not printers:
        return result
    printer_ids = [printer.id for printer in printers]

    readings = (
        await db.execute(
            select(
                PrinterCounterReading.printer_id,
                PrinterCounterReading.recorded_at,
                PrinterCounterReading.page_count_total,
            )
            .where(
                PrinterCounterReading.printer_id.in_(printer_ids),
                PrinterCounterReading.recorded_at >= since,
                PrinterCounterReading.page_count_total.is_not(None),
            )
            .order_by(PrinterCounterReading.printer_id, PrinterCounterReading.recorded_at)
        )
    ).all()

    jobs = (
        await db.execute(
            select(
                Job.printer_id,
                Job.completed_at,
                # The printer's own count when it gave one, and the count
                # taken from the document before delivery only when it did
                # not.
                #
                # That order looks backwards for a check whose whole
                # purpose is to doubt the printer, and it is deliberate.
                # `submitted_pages` is every page of the PDF times copies,
                # which is not always what the printer was asked to
                # produce: someone printing pages 2-3 of a ten-page
                # document sends two, and preferring the document's count
                # would invent eight pages and report the printer for
                # losing them. Reversing this trades a blind spot for
                # false alarms, and a false alarm is the one thing this
                # feature must never produce.
                #
                # So it fills the 11.5% of jobs that had no count at all
                # rather than overruling the 88.5% that did.
                func.coalesce(Job.page_count, Job.submitted_pages).label("pages"),
                Job.duplex,
                Job.document_name,
            ).where(
                Job.printer_id.in_(printer_ids),
                Job.status == "forwarded",
                Job.completed_at.is_not(None),
                Job.completed_at >= since,
                func.coalesce(Job.page_count, Job.submitted_pages).is_not(None),
                func.coalesce(Job.page_count, Job.submitted_pages) > 0,
                # A retried job leaves a trail of rows for one delivery
                # (app/printers/job_reconcile.py) and only the last of them
                # describes what was actually sent. Counting the others would
                # invent pages the printer was never given and then report the
                # printer for not printing them.
                Job.superseded_by_job_id.is_(None),
                # Follow-Me jobs are held against the printer they were aimed
                # at and released at whichever printer the user walks up to
                # (app/routers/release.py does not move printer_id). Their
                # pages therefore land on another device's counter, and
                # charging them here would report every Follow-Me printer in
                # the building for losing work that printed perfectly well.
                or_(Job.hold_reason.is_(None), Job.hold_reason != "follow_me"),
            )
        )
    ).all()

    readings_by_printer: dict = {}
    for printer_id, recorded_at, total in readings:
        readings_by_printer.setdefault(printer_id, []).append((_aware(recorded_at), total))

    jobs_by_printer: dict = {}
    for printer_id, completed_at, pages, duplex, document_name in jobs:
        jobs_by_printer.setdefault(printer_id, []).append(
            SentJob(
                completed_at=_aware(completed_at),
                pages=pages,
                duplex=duplex,
                document_name=document_name,
            )
        )

    for printer in printers:
        printer_readings = readings_by_printer.get(printer.id, [])
        if len(printer_readings) < 2:
            # No counter, or not enough of one to measure anything against.
            # Left exactly as it is rather than cleared: a printer whose SNMP
            # poll has stopped answering has not been shown to be healthy, and
            # STALE_AFTER already retires the verdict on its own schedule.
            continue
        result.assessed += 1
        verdict = assess(printer_readings, jobs_by_printer.get(printer.id, []))
        if verdict is None:
            if printer.pages_not_printed is not None:
                printer.pages_not_printed = None
                result.cleared += 1
            continue
        # Stamped with the newest reading the verdict was actually computed
        # from, not with the clock. A printer whose SNMP poll has stopped
        # answering still has yesterday's readings inside the 24-hour window,
        # so a sweep every thirty minutes would keep re-deciding the same old
        # shortfall and stamping it as fresh — and STALE_AFTER would never
        # retire a warning about a printer nobody can currently see. A healthy
        # printer's newest reading is at most one poll old, which is well
        # inside that timeout, so this costs nothing in the normal case.
        printer.pages_not_printed = to_record(verdict, printer_readings[-1][0])
        result.flagged += 1
        logger.warning(
            "%s: %d pages sent in the last 24h are unaccounted for by its page counter (%s).",
            printer.name,
            verdict.shortfall,
            ", ".join(verdict.documents[:MAX_DOCUMENTS_NAMED]) or "no document names recorded",
        )

    return result


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes for timezone-aware columns; Postgres
    does not. Comparisons here mix the two, so everything is normalised on the
    way in."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
