"""Jobs sent to a printer that is switched off.

CUPS queues behind an absent printer by itself, but only for MaxJobTime — the
default is three hours, after which cupsd cancels the job. On 2026-08-23 a
teacher's job went to a classroom printer that was off for the night at 17:56;
it was due to be destroyed at 20:56, with no notice to her and nothing in the
queue by morning.

So PrintOps holds it instead: it waits as long as it needs to, it is visible on
the Held Jobs page while it waits, and it goes the moment the printer answers.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.job import Job
from app.models.printer import Printer
from app.printers import offline_holds
from app.printers.release import ReleaseError
from app.quotas.service import resolve_hold_reason


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _printer(session, status="offline", **kwargs):
    printer = Printer(
        id=uuid.uuid4(),
        name="ES 4th Grade Printer",
        ip_address="10.20.3.104",
        status=status,
        **kwargs,
    )
    session.add(printer)
    await session.commit()
    return printer


async def _held(session, printer, path="__auto__", created=None):
    if path == "__auto__":
        path = f"/var/spool/printops-held/{uuid.uuid4()}"
    job = Job(
        id=uuid.uuid4(),
        printer_id=printer.id,
        status="held",
        hold_reason=offline_holds.HOLD_REASON,
        submitted_by="sayers@brookfieldr3.org",
        document_name="4th Grade Reading Challenge",
        held_file_path=path,
        created_at=created or datetime.now(UTC),
    )
    session.add(job)
    await session.commit()
    return job


# ---- deciding to hold ----


async def test_a_job_for_an_offline_printer_is_held(session):
    printer = await _printer(session, status="offline")
    assert await resolve_hold_reason(session, printer, "sayers@") == "printer_offline"


async def test_a_job_for_a_working_printer_is_not(session):
    printer = await _printer(session, status="online")
    assert await resolve_hold_reason(session, printer, "sayers@") is None


async def test_release_at_the_printer_still_wins(session):
    """The person is standing at the device; that is how their job gets
    delivered, and it is a better answer than "waiting for the printer"."""
    printer = await _printer(session, status="offline", release_required=True)
    assert await resolve_hold_reason(session, printer, "sayers@") == "pin_release"


# ---- letting them go again ----


async def test_coming_back_releases_what_was_waiting(session):
    printer = await _printer(session)
    job = await _held(session, printer)

    with patch.object(offline_holds, "submit_released_job", return_value="request id is x-1"):
        released = await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    assert released == 1
    await session.refresh(job)
    assert job.status == "forwarded"
    assert job.held_file_path is None


async def test_they_go_in_the_order_they_were_sent(session):
    """The order people pressed print. A queue that reorders itself under load
    is one nobody can predict."""
    printer = await _printer(session)
    first = await _held(session, printer, created=datetime(2026, 8, 23, 17, 0, tzinfo=UTC))
    second = await _held(session, printer, created=datetime(2026, 8, 23, 18, 0, tzinfo=UTC))
    expected = [first.held_file_path, second.held_file_path]
    sent = []

    def _record(printer_id, path, *args):
        sent.append(path)
        return "request id is x"

    with patch.object(offline_holds, "submit_released_job", side_effect=_record):
        await offline_holds.release_jobs_waiting_for(session, printer)

    assert sent == expected


async def test_a_delivery_that_fails_is_recorded_rather_than_retried_forever(session):
    printer = await _printer(session)
    job = await _held(session, printer)

    with patch.object(
        offline_holds, "submit_released_job", side_effect=ReleaseError("lp: no such queue")
    ):
        released = await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    assert released == 0
    await session.refresh(job)
    assert job.status == "failed"
    assert "no such queue" in job.error_message


async def test_a_hold_with_no_document_is_closed_not_left_promising_a_print(session):
    printer = await _printer(session)
    job = await _held(session, printer, path=None)

    with patch.object(offline_holds, "submit_released_job", return_value="x"):
        await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    await session.refresh(job)
    assert job.status == "failed"


async def test_other_kinds_of_hold_are_left_alone(session):
    """A job waiting for someone's PIN is not waiting for the printer."""
    printer = await _printer(session)
    job = await _held(session, printer)
    job.hold_reason = "pin_release"
    await session.commit()

    with patch.object(offline_holds, "submit_released_job", return_value="x") as submit:
        released = await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    assert released == 0
    assert submit.call_count == 0
    await session.refresh(job)
    assert job.status == "held"


async def test_nothing_waiting_is_not_an_error(session):
    printer = await _printer(session)
    assert await offline_holds.release_jobs_waiting_for(session, printer) == 0


async def test_only_this_printers_jobs_are_released(session):
    printer = await _printer(session)
    other = await _printer(session)
    other.name = "Some Other Printer"
    await session.commit()
    theirs = await _held(session, other)
    await _held(session, printer)

    with patch.object(offline_holds, "submit_released_job", return_value="x"):
        released = await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    assert released == 1
    await session.refresh(theirs)
    assert theirs.status == "held"


async def test_every_waiting_job_is_accounted_for(session):
    printer = await _printer(session)
    for _ in range(3):
        await _held(session, printer)

    with patch.object(offline_holds, "submit_released_job", return_value="x"):
        await offline_holds.release_jobs_waiting_for(session, printer)
    await session.commit()

    rows = (await session.execute(select(Job).where(Job.printer_id == printer.id))).scalars().all()
    assert {row.status for row in rows} == {"forwarded"}


# ---- the clock that would have eaten the weekend ----


async def test_a_job_waiting_for_a_printer_is_given_no_expiry(session):
    """The release-hold expiry exists so uncollected printing does not sit on
    the server forever. Nobody has failed to collect this one — and at the
    48-hour default it would be deleted on Sunday, for a printer switched off
    on Friday, which is precisely the weekend this feature exists to survive."""
    from app.routers.jobs import update_job
    from app.schemas.job import JobUpdate

    printer = await _printer(session)
    job = Job(
        id=uuid.uuid4(),
        printer_id=printer.id,
        status="forwarding",
        hold_reason=offline_holds.HOLD_REASON,
        submitted_by="sayers@brookfieldr3.org",
    )
    session.add(job)
    await session.commit()

    await update_job(
        job.id,
        JobUpdate(status="held", held_file_path="/var/spool/printops-held/y"),
        db=session,
    )

    await session.refresh(job)
    assert job.status == "held"
    assert job.held_expires_at is None


async def test_an_ordinary_release_hold_still_expires(session):
    """Unchanged for the case the expiry was written for."""
    from app.routers.jobs import update_job
    from app.schemas.job import JobUpdate

    printer = await _printer(session, status="online")
    job = Job(
        id=uuid.uuid4(),
        printer_id=printer.id,
        status="forwarding",
        hold_reason="pin_release",
        submitted_by="someone@brookfieldr3.org",
    )
    session.add(job)
    await session.commit()

    await update_job(
        job.id,
        JobUpdate(status="held", held_file_path="/var/spool/printops-held/z"),
        db=session,
    )

    await session.refresh(job)
    assert job.held_expires_at is not None


# ---- telling "away for the night" apart from "nobody can reach this" ----


async def test_a_printer_that_has_never_answered_is_reported_at_once(session):
    """The case that cost a day: ES 4th Grade Printer was recorded at
    10.20.3.104 and was actually at 10.10.1.10, so it read as offline and
    everyone — me included — assumed it was switched off for the night. There
    is nothing to wait for at an address nothing answers on."""
    printer = await _printer(session)
    printer.last_online_at = None
    await _held(session, printer)

    alert = await offline_holds.waiting_alert(session, printer)

    assert alert is not None
    assert "never answered" in alert
    assert printer.ip_address in alert


async def test_a_printer_away_for_the_night_says_nothing(session):
    """Ordinary, and the jobs are safe. An alarm that fires every evening is
    one nobody reads by October."""
    printer = await _printer(session)
    printer.last_online_at = datetime.now(UTC) - timedelta(hours=10)
    await _held(session, printer)

    assert await offline_holds.waiting_alert(session, printer) is None


async def test_a_printer_away_more_than_a_day_with_work_behind_it_does(session):
    printer = await _printer(session)
    printer.last_online_at = datetime.now(UTC) - timedelta(days=3)
    await _held(session, printer)

    alert = await offline_holds.waiting_alert(session, printer)

    assert alert is not None
    assert "3 days" in alert


async def test_an_absent_printer_with_no_work_behind_it_says_nothing(session):
    """Nobody is waiting, so there is nothing to act on."""
    printer = await _printer(session)
    printer.last_online_at = None

    assert await offline_holds.waiting_alert(session, printer) is None


async def test_the_alert_counts_the_jobs_and_ages_the_oldest(session):
    printer = await _printer(session)
    printer.last_online_at = None
    await _held(session, printer, created=datetime.now(UTC) - timedelta(hours=30))
    await _held(session, printer)
    await _held(session, printer)

    alert = await offline_holds.waiting_alert(session, printer)

    assert "3 jobs waiting" in alert
    assert "1 day" in alert


async def test_one_job_is_not_described_as_jobs(session):
    printer = await _printer(session)
    printer.last_online_at = None
    await _held(session, printer)

    assert "1 job waiting" in await offline_holds.waiting_alert(session, printer)


async def test_a_printer_that_answers_but_is_jammed_still_counts_as_reached(session):
    """derive_status calls a printer that answers and reports a jam "error".
    Recording reachability only for "online" made such a printer look like one
    that had never answered — so a wrong-address alert for a machine somebody
    had been talking to all afternoon."""
    from unittest.mock import patch

    from app.printers import status as status_module
    from app.printers.ipp_client import PrinterStateResult

    printer = await _printer(session, status="online")

    async def _jammed(*_a, **_k):
        return PrinterStateResult(
            printer_state=3, state_reasons=["media-jam-error"], state_message=None
        )

    with (
        patch.object(status_module, "probe_printer_state", _jammed),
        patch.object(status_module, "_apply_queue_recovery", _false),
        patch.object(status_module, "_apply_queue_stall", _none),
    ):
        await status_module.refresh_printer_status(printer)

    assert printer.status == "error", "it is jammed, and that is still reported"
    assert printer.last_online_at is not None, "but it was reached"


async def _false(*_a, **_k):
    return False


async def _none(*_a, **_k):
    return None
