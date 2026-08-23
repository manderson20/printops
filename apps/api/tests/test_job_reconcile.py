"""app/printers/job_reconcile.py — closing out jobs whose backend never
reported.

The fault: a `jobs` row is written when delivery starts and only becomes
terminal when the CUPS backend script PATCHes it. Every way that script can die
without reporting — cupsd signalling it, an API restart eating the final
best-effort PATCH — leaves a row that says a job is still printing, forever.
They accumulated at 2-3% of every job on this server from July, unnoticed,
until 107 of them were counted on 2026-08-23: two of the newest still had a
CUPS record, and it said job-state 7, canceled.

What these guard is mostly what the sweep must *not* do. It resolves a user's
printing history and, through it, their quota — being wrong is worse than being
late, and every uncertain case is left alone rather than guessed at.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.printers import job_control, job_reconcile
from app.printers.job_control import CupsJobOutcome
from app.printers.job_reconcile import STUCK_AFTER, UNRESOLVABLE_AFTER, reconcile_stuck_jobs

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=6)

COMPLETED = CupsJobOutcome(
    state=9,
    reason="job-completed-successfully",
    page_count=46,
    color_mode="monochrome",
    duplex=False,
    paper_size="Letter",
)
CANCELED = CupsJobOutcome(state=7, reason="job-canceled-by-user")
ABORTED = CupsJobOutcome(state=8, reason="job-aborted-by-system")
PROCESSING = CupsJobOutcome(state=5, reason="job-printing")
NO_RECORD = CupsJobOutcome(state=None)


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
    row = Printer(name="ES Veronica Copier", ip_address="10.10.3.36")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _cups_says(monkeypatch, outcome, seen=None):
    async def fake_lookup(printer_id, cups_job_id):
        if seen is not None:
            seen.append((printer_id, cups_job_id))
        return outcome

    monkeypatch.setattr(job_reconcile, "_run_lookup", fake_lookup)


async def _add_job(db, printer, *, created_at, cups_job_id=4584, status="forwarding"):
    job = Job(
        id=uuid.uuid4(),
        printer_id=printer.id,
        cups_job_id=cups_job_id,
        status=status,
        submitted_by="ateacher",
        document_name="Cupcake toppers - Google Docs",
    )
    db.add(job)
    await db.commit()
    # created_at is a server-default column, so it has to be pushed back
    # explicitly to age a row.
    job.created_at = created_at
    await db.commit()
    return job


@pytest.mark.asyncio
async def test_a_job_cups_saw_finish_is_recorded_as_printed(db, printer, monkeypatch):
    """The API-restart case: the print happened, the row just never heard.
    The page count is taken from the same CUPS record the backend would have
    read, so the job isn't left in the reports with nothing against it."""
    job = await _add_job(db, printer, created_at=LONG_AGO)
    _cups_says(monkeypatch, COMPLETED)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.resolved == 1
    await db.refresh(job)
    assert job.status == "forwarded"
    assert job.page_count == 46
    assert job.color_mode == "monochrome"
    assert job.paper_size == "Letter"
    # SQLite hands datetimes back without their timezone; Postgres doesn't.
    assert job.completed_at.replace(tzinfo=UTC) == NOW
    assert job.error_message is None


@pytest.mark.asyncio
async def test_a_job_cups_cancelled_is_not_recorded_as_a_failure(db, printer, monkeypatch):
    """job-state 7 is what the two newest stranded rows actually said. cupsd
    cancels a backend's job when it is cancelled, held or restarted — none of
    which is a fault anyone can act on."""
    job = await _add_job(db, printer, created_at=LONG_AGO)
    _cups_says(monkeypatch, CANCELED)

    await reconcile_stuck_jobs(db, now=NOW)

    await db.refresh(job)
    assert job.status == "cancelled"
    assert "Cancelled at the print server" in job.error_message


@pytest.mark.asyncio
async def test_a_job_cups_aborted_is_recorded_as_failed_with_its_reason(db, printer, monkeypatch):
    job = await _add_job(db, printer, created_at=LONG_AGO)
    _cups_says(monkeypatch, ABORTED)

    await reconcile_stuck_jobs(db, now=NOW)

    await db.refresh(job)
    assert job.status == "failed"
    assert "job-aborted-by-system" in job.error_message


@pytest.mark.asyncio
async def test_a_job_still_printing_is_left_alone_however_old_the_row_is(db, printer, monkeypatch):
    """A 26 MB Photoshop file at 600dpi expands to a ~166 MB raster, and
    graphic arts sends those all day. Age is not evidence of anything; cupsd's
    answer is."""
    job = await _add_job(db, printer, created_at=NOW - timedelta(hours=5))
    _cups_says(monkeypatch, PROCESSING)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.resolved == 0
    assert result.left_in_flight == 1
    await db.refresh(job)
    assert job.status == "forwarding"


@pytest.mark.asyncio
async def test_a_job_still_young_is_not_even_asked_about(db, printer, monkeypatch):
    seen = []
    await _add_job(db, printer, created_at=NOW - STUCK_AFTER + timedelta(minutes=1))
    _cups_says(monkeypatch, COMPLETED, seen)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.resolved == 0
    assert seen == []


@pytest.mark.asyncio
async def test_a_job_cups_has_no_record_of_waits_before_being_written_off(db, printer, monkeypatch):
    """cupsd could be restarting, or a queue could have been rebuilt under a
    running job. Neither is a reason to write off someone's print."""
    job = await _add_job(db, printer, created_at=NOW - UNRESOLVABLE_AFTER + timedelta(minutes=5))
    _cups_says(monkeypatch, NO_RECORD)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.unresolvable_kept == 1
    await db.refresh(job)
    assert job.status == "forwarding"


@pytest.mark.asyncio
async def test_a_job_nobody_can_account_for_says_so_rather_than_claiming_a_failure(
    db, printer, monkeypatch
):
    """cupsd rolls job history off by count, not age, so for the six-week
    backlog there is genuinely no answer to be had. A failure count is the one
    number an admin acts on, and this is not evidence of one."""
    job = await _add_job(db, printer, created_at=LONG_AGO)
    _cups_says(monkeypatch, NO_RECORD)

    await reconcile_stuck_jobs(db, now=NOW)

    await db.refresh(job)
    assert job.status == "cancelled"
    assert "Outcome unknown" in job.error_message
    assert job.completed_at.replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_a_restarted_jobs_earlier_row_never_takes_the_same_pages_twice(
    db, printer, monkeypatch
):
    """cupsd restarting a job runs the backend again, and the backend writes a
    *new* row. 58 of the 107 stranded rows had a later sibling like this. If
    each one resolved from cupsd's record of that job they would each take a
    copy of the same page count, and one print would land three times in
    everyone's totals."""
    first = await _add_job(db, printer, created_at=LONG_AGO, cups_job_id=4584)
    second = await _add_job(
        db, printer, created_at=LONG_AGO + timedelta(minutes=1), cups_job_id=4584
    )
    _cups_says(monkeypatch, COMPLETED)

    await reconcile_stuck_jobs(db, now=NOW)

    await db.refresh(first)
    await db.refresh(second)
    assert first.status == "cancelled"
    assert first.page_count is None
    assert "Superseded" in first.error_message
    # The last attempt is the one that saw the delivery through, so it is the
    # one that keeps what was printed.
    assert second.status == "forwarded"
    assert second.page_count == 46


@pytest.mark.asyncio
async def test_the_same_cups_job_number_on_another_printer_is_not_a_sibling(
    db, printer, monkeypatch
):
    """CUPS job numbers are per-server, but the pairing still has to be
    checked as a pair — matching the two columns independently would call two
    unrelated jobs the same job."""
    other = Printer(name="LCACTC - GA Kyocera", ip_address="10.50.1.37")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    mine = await _add_job(db, printer, created_at=LONG_AGO, cups_job_id=4584)
    await _add_job(db, other, created_at=LONG_AGO + timedelta(minutes=1), cups_job_id=4584)
    _cups_says(monkeypatch, COMPLETED)

    await reconcile_stuck_jobs(db, now=NOW)

    await db.refresh(mine)
    assert mine.status == "forwarded"


@pytest.mark.asyncio
async def test_a_cupsd_that_will_not_answer_changes_nothing(db, printer, monkeypatch):
    """Being wrong here is worse than being late. A row cupsd couldn't be
    asked about is left exactly as it is for the next sweep."""
    job = await _add_job(db, printer, created_at=LONG_AGO)

    async def no_answer(_printer_id, _cups_job_id):
        return None

    monkeypatch.setattr(job_reconcile, "_run_lookup", no_answer)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.unasked == 1
    await db.refresh(job)
    assert job.status == "forwarding"


@pytest.mark.asyncio
async def test_a_cupsd_that_answers_with_an_error_is_not_read_as_no_record(
    db, printer, monkeypatch
):
    """An IPP error is cupsd declining to answer, not cupsd saying the job
    never existed — the distinction the two-hour write-off rests on. This
    goes through the real cups_job_outcome rather than stubbing the lookup,
    since the conflation being guarded against lived in that parsing."""
    job = await _add_job(db, printer, created_at=LONG_AGO)
    monkeypatch.setattr(
        job_control,
        "_ipptool_plist",
        lambda _queue, _request: (
            '<plist version="1.0"><dict><key>StatusCode</key>'
            "<string>client-error-not-authorized</string></dict></plist>"
        ),
    )

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.unasked == 1
    assert result.resolved == 0
    await db.refresh(job)
    assert job.status == "forwarding"


@pytest.mark.asyncio
async def test_terminal_jobs_are_never_revisited(db, printer, monkeypatch):
    """Held jobs especially: a held job is waiting for a person, not stuck."""
    seen = []
    for status in ("forwarded", "failed", "cancelled", "held"):
        await _add_job(db, printer, created_at=LONG_AGO, status=status, cups_job_id=hash(status))
    _cups_says(monkeypatch, COMPLETED, seen)

    result = await reconcile_stuck_jobs(db, now=NOW)

    assert result.resolved == 0
    assert seen == []


@pytest.mark.asyncio
async def test_a_sweep_takes_the_oldest_first_and_leaves_the_rest_for_next_time(
    db, printer, monkeypatch
):
    """Six weeks of backlog is one ipptool round trip per row. The cap keeps
    the first sweep after an upgrade from becoming a burst at cupsd."""
    for minute in range(5):
        await _add_job(
            db, printer, created_at=LONG_AGO + timedelta(minutes=minute), cups_job_id=4500 + minute
        )
    _cups_says(monkeypatch, COMPLETED)

    result = await reconcile_stuck_jobs(db, now=NOW, limit=2)

    assert result.resolved == 2
    remaining = (
        (await db.execute(select(Job).where(Job.status == "forwarding").order_by(Job.created_at)))
        .scalars()
        .all()
    )
    assert [job.cups_job_id for job in remaining] == [4502, 4503, 4504]
