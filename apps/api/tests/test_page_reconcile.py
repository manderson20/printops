"""app/printers/page_reconcile.py — noticing pages that were sent and never
came out.

The fault: a printer can accept a job, report `job-completed-successfully`, and
print nothing at all. An HP LaserJet M607 did exactly that to the same PDF
three times across 2026-09-03 and 2026-09-04, while CUPS, the PrintOps backend
and the printer's own job history all recorded a successful print. The only
witness is the page counter, and until now nothing compared it against what
PrintOps had sent.

What these guard is mostly the silence. A diagnostic that cries wolf gets
scrolled past, and this one is looking at two numbers gathered by completely
different means — a CUPS page count and an SNMP counter — with walk-up copying
free to move one of them and not the other. Every case below where the module
declines to report something is a case where reporting it would have been a
guess.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.models.snmp import PrinterCounterReading
from app.printers import page_reconcile, status
from app.printers.page_reconcile import (
    MIN_SHORTFALL,
    PAGES_NOT_PRINTED_REASON,
    STALE_AFTER,
    SentJob,
    assess,
    recorded_reason,
    sweep_unprinted_pages,
    to_record,
)

NOW = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)


def _readings(*pairs):
    """[(minutes before NOW, counter total), ...] -> the shape assess() takes."""
    return [(NOW - timedelta(minutes=minutes), total) for minutes, total in pairs]


def _job(minutes_before, pages, *, duplex=False, name="2026 scavenger hunt.pdf"):
    return SentJob(
        completed_at=NOW - timedelta(minutes=minutes_before),
        pages=pages,
        duplex=duplex,
        document_name=name,
    )


# --- the arithmetic ------------------------------------------------------


def test_reports_the_window_that_lost_pages():
    """The case this was written for, at the numbers it actually happened at:
    46 pages went to the Ag BW printer between two counter reads and the
    counter moved 16."""
    verdict = assess(
        _readings((60, 104_433), (30, 104_449)),
        [
            _job(50, 9, name="Chapter Challenge Spreadsheet - Google Sheets"),
            _job(45, 10, name="FFA Scavenger Hunt - Group Cards"),
            _job(40, 10, name="FFA Scavenger Hunt - Group Cards"),
            _job(35, 10, name="2026 scavenger hunt.pdf"),
            _job(33, 7, name="Fall Kickoff Scavenger Hunt Locations - Google Slides"),
        ],
    )

    assert verdict is not None
    assert verdict.shortfall == 30
    assert verdict.jobs == 5
    # De-duplicated, so a document that failed twice is named once.
    assert verdict.documents.count("FFA Scavenger Hunt - Group Cards") == 1


def test_says_nothing_when_the_counter_kept_up():
    assert assess(_readings((60, 104_485), (30, 104_508)), [_job(45, 23)]) is None


def test_ignores_a_shortfall_too_small_to_mean_anything():
    """A page count and an SNMP counter are gathered by different means, and
    the odd page between them is ordinary rather than evidence."""
    assert (
        assess(_readings((60, 1_000), (30, 1_000 + 10 - (MIN_SHORTFALL - 1))), [_job(45, 10)])
        is None
    )


def test_ignores_a_busy_window_that_lost_only_a_fraction():
    """200 sent and 190 counted is arithmetic, not a printer throwing work
    away — even though ten pages clears MIN_SHORTFALL on its own."""
    assert assess(_readings((60, 5_000), (30, 5_190)), [_job(45, 200)]) is None


def test_ignores_a_counter_that_went_backwards():
    """A reset, a replaced formatter, a counter that rolled: whatever happened,
    the delta is not a measurement of this window's printing."""
    assert assess(_readings((60, 90_000), (30, 12)), [_job(45, 40)]) is None


def test_a_duplex_job_is_judged_at_its_lowest_plausible_cost():
    """Counters are per impression on most devices and per sheet on some, and
    PrintOps does not know which. A ten-page duplex job that moved the counter
    by five has to read as printed, or every duplex printer on the fleet is
    reported for pages that are on paper."""
    assert assess(_readings((60, 400), (30, 405)), [_job(45, 10, duplex=True)]) is None
    # ...and the same job moving it by nothing at all is still reported.
    verdict = assess(_readings((60, 400), (30, 400)), [_job(45, 10, duplex=True)])
    assert verdict is not None and verdict.shortfall == 5


def test_a_job_after_the_last_reading_is_held_over():
    """The counter cannot have seen a job taken after the last poll of it, so
    that job waits for the next sweep instead of being reported against a
    counter that has not been read since."""
    assert assess(_readings((60, 300), (30, 300)), [_job(5, 40)]) is None


def test_needs_two_readings_to_measure_anything():
    assert assess(_readings((30, 300)), [_job(20, 40)]) is None


def test_each_window_is_judged_on_its_own():
    """A clean window does not launder the one next to it."""
    verdict = assess(
        _readings((90, 1_000), (60, 1_000), (30, 1_040)),
        [_job(75, 20, name="lost.pdf"), _job(45, 40, name="printed.pdf")],
    )
    assert verdict is not None
    assert verdict.shortfall == 20
    assert verdict.documents == ("lost.pdf",)


# --- the message ---------------------------------------------------------


def test_the_message_names_the_documents_and_the_size_of_the_loss():
    verdict = assess(
        _readings((60, 100), (30, 100)),
        [_job(45, 10, name="FFA Scavenger Hunt - Group Cards")],
    )
    reason = page_reconcile.unprinted_reason(verdict)
    assert "10 pages" in reason
    assert "FFA Scavenger Hunt - Group Cards" in reason
    # Says what it cannot know rather than naming a cause it has no way to
    # establish — an admin acts on this, and a wrong cause sends them at the
    # wrong thing.
    assert "usually" in reason


def test_a_stale_verdict_is_not_reported():
    """The sweep rewrites the row every cycle, so a record this old means the
    sweep stopped — not that pages are going missing right now."""
    verdict = assess(_readings((60, 100), (30, 100)), [_job(45, 40)])
    record = to_record(verdict, NOW)
    assert recorded_reason(record, NOW) is not None
    assert recorded_reason(record, NOW + STALE_AFTER + timedelta(minutes=1)) is None


def test_a_malformed_record_is_dropped_rather_than_raised():
    """A rolling diagnostic loses its own entry rather than taking the status
    poll down with it."""
    assert recorded_reason({"shortfall": "lots"}, NOW) is None
    assert recorded_reason({}, NOW) is None
    assert recorded_reason(None, NOW) is None


# --- the sweep -----------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def printer(db):
    row = Printer(name="LCACTC - Ag BW Printer", ip_address="10.30.1.217", status="online")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _reading(db, printer, minutes_before, total):
    db.add(
        PrinterCounterReading(
            printer_id=printer.id,
            recorded_at=NOW - timedelta(minutes=minutes_before),
            page_count_total=total,
        )
    )
    await db.commit()


async def _forwarded(db, printer, minutes_before, pages, **kwargs):
    job = Job(
        id=uuid.uuid4(),
        printer_id=printer.id,
        status="forwarded",
        submitted_by="jordan.staff@example.org",
        document_name="FFA Scavenger Hunt - Group Cards",
        page_count=pages,
        duplex=False,
        completed_at=NOW - timedelta(minutes=minutes_before),
        **kwargs,
    )
    db.add(job)
    await db.commit()
    return job


@pytest.mark.asyncio
async def test_sweep_records_the_verdict_on_the_row(db, printer):
    await _reading(db, printer, 60, 104_509)
    await _reading(db, printer, 30, 104_509)
    await _forwarded(db, printer, 45, 10)

    result = await sweep_unprinted_pages(db, now=NOW)

    assert result.flagged == 1
    assert printer.pages_not_printed is not None
    assert printer.pages_not_printed["shortfall"] == 10


@pytest.mark.asyncio
async def test_sweep_clears_a_verdict_once_the_printer_is_clean(db, printer):
    printer.pages_not_printed = {"shortfall": 99, "windows": 1, "assessed_at": NOW.isoformat()}
    await _reading(db, printer, 60, 100)
    await _reading(db, printer, 30, 140)
    await _forwarded(db, printer, 45, 40)

    result = await sweep_unprinted_pages(db, now=NOW)

    assert result.cleared == 1
    assert printer.pages_not_printed is None


@pytest.mark.asyncio
async def test_sweep_leaves_a_printer_it_cannot_measure_alone(db, printer):
    """No usable counter is not a clean bill of health. The verdict stays as it
    is and STALE_AFTER retires it on its own schedule."""
    printer.pages_not_printed = {"shortfall": 99, "windows": 1, "assessed_at": NOW.isoformat()}
    await _reading(db, printer, 30, 100)
    await _forwarded(db, printer, 20, 40)

    result = await sweep_unprinted_pages(db, now=NOW)

    assert result.assessed == 0
    assert printer.pages_not_printed is not None


@pytest.mark.asyncio
async def test_sweep_ignores_follow_me_jobs(db, printer):
    """A Follow-Me job is held against the printer it was aimed at and printed
    at whichever one the user walked up to — its pages land on another
    device's counter, and charging them here would report every Follow-Me
    printer in the building."""
    await _reading(db, printer, 60, 100)
    await _reading(db, printer, 30, 100)
    await _forwarded(db, printer, 45, 40, hold_reason="follow_me")

    assert (await sweep_unprinted_pages(db, now=NOW)).flagged == 0
    assert printer.pages_not_printed is None


@pytest.mark.asyncio
async def test_sweep_ignores_superseded_retries(db, printer):
    """One delivery that cupsd retried leaves several rows and only the last
    describes what was sent. Counting the others invents pages the printer was
    never given, then reports it for not printing them."""
    await _reading(db, printer, 60, 100)
    await _reading(db, printer, 30, 100)
    await _forwarded(db, printer, 45, 40, superseded_by_job_id=uuid.uuid4())

    assert (await sweep_unprinted_pages(db, now=NOW)).flagged == 0


@pytest.mark.asyncio
async def test_sweep_ignores_jobs_that_did_not_forward(db, printer):
    await _reading(db, printer, 60, 100)
    await _reading(db, printer, 30, 100)
    job = await _forwarded(db, printer, 45, 40)
    job.status = "cancelled"
    await db.commit()

    assert (await sweep_unprinted_pages(db, now=NOW)).flagged == 0


# --- how the status poll renders it --------------------------------------


def test_status_poll_warns_without_calling_the_printer_broken():
    """Amber, not red: the printer is online and taking work, and a red badge
    on a machine printing normally right now is how people learn to stop
    reading the field."""
    verdict = assess(_readings((60, 100), (30, 100)), [_job(45, 40)])
    printer = Printer(name="LCACTC - Ag BW Printer", status="online")
    printer.pages_not_printed = to_record(verdict, datetime.now(UTC))

    status._apply_pages_not_printed(printer)

    assert printer.status == "online"
    assert PAGES_NOT_PRINTED_REASON in (printer.status_reasons or [])
    assert "cannot be accounted for" in (printer.status_message or "")


def test_status_poll_leaves_a_printer_with_a_live_fault_alone():
    """An offline printer has said something about right now, and yesterday's
    output does not belong on top of it."""
    verdict = assess(_readings((60, 100), (30, 100)), [_job(45, 40)])
    printer = Printer(name="LCACTC - Ag BW Printer", status="offline")
    printer.pages_not_printed = to_record(verdict, datetime.now(UTC))
    printer.status_message = "No response from the printer."

    status._apply_pages_not_printed(printer)

    assert printer.status_reasons is None
    assert printer.status_message == "No response from the printer."


async def test_a_verdict_is_dated_by_its_readings_not_by_the_clock(db, printer):
    """A printer whose SNMP poll has stopped answering still has yesterday's
    readings inside the 24-hour window. Without this, every sweep would
    re-decide the same old shortfall and stamp it as fresh, and STALE_AFTER
    would never retire a warning about a printer nobody can currently see."""
    last_seen_minutes = 6 * 60
    await _reading(db, printer, last_seen_minutes + 30, 1000)
    await _reading(db, printer, last_seen_minutes, 1000)
    db.add(
        Job(
            printer_id=printer.id,
            status="forwarded",
            page_count=40,
            completed_at=NOW - timedelta(minutes=last_seen_minutes + 10),
            created_at=NOW - timedelta(minutes=last_seen_minutes + 10),
        )
    )
    await db.commit()

    await sweep_unprinted_pages(db, now=NOW)
    await db.commit()
    await db.refresh(printer)

    record = printer.pages_not_printed
    assert record is not None
    assessed_at = datetime.fromisoformat(record["assessed_at"])
    # The newest reading, six hours old — not NOW.
    assert abs((assessed_at - (NOW - timedelta(minutes=last_seen_minutes))).total_seconds()) < 5
    # And therefore already past the point where the status line retires it.
    assert recorded_reason(record, now=NOW) is None


async def test_a_job_the_printer_never_counted_is_still_measured(db, printer):
    """The gap this was built to close.

    11.5% of forwarded jobs over thirty days came back with no page count
    at all, and a job with no page count contributes nothing to a window —
    so a printer could accept work, print none of it, and raise no
    shortfall because nothing believed it had been sent anything. A count
    taken from the document before delivery is the figure that does not
    depend on the printer agreeing to report.
    """
    await _reading(db, printer, 90, 5000)
    await _reading(db, printer, 30, 5000)
    db.add(
        Job(
            printer_id=printer.id,
            status="forwarded",
            page_count=None,
            submitted_pages=40,
            completed_at=NOW - timedelta(minutes=60),
            created_at=NOW - timedelta(minutes=60),
        )
    )
    await db.commit()

    await sweep_unprinted_pages(db, now=NOW)
    await db.commit()
    await db.refresh(printer)

    assert printer.pages_not_printed is not None
    assert printer.pages_not_printed["shortfall"] == 40


async def test_the_printers_own_count_still_governs_when_it_has_one(db, printer):
    """Only the gap is filled.

    The document's page count is every page times copies, which is not
    always what the printer was asked to produce — someone printing pages
    2-3 of a ten-page file sends two. Preferring it would invent eight
    pages and report the printer for losing them, which is the one thing
    this feature must never do."""
    await _reading(db, printer, 90, 5000)
    await _reading(db, printer, 30, 5000)
    db.add(
        Job(
            printer_id=printer.id,
            status="forwarded",
            page_count=40,
            submitted_pages=999,
            completed_at=NOW - timedelta(minutes=60),
            created_at=NOW - timedelta(minutes=60),
        )
    )
    await db.commit()

    await sweep_unprinted_pages(db, now=NOW)
    await db.commit()
    await db.refresh(printer)

    assert printer.pages_not_printed["shortfall"] == 40
