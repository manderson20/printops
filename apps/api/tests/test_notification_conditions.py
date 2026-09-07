"""Whether a condition is worth telling somebody about.

This is the file that decides whether anybody keeps notifications switched on.
Delivery is a POST; the risk is crying wolf, and a notifier people mute is
worse than none — because the message that mattered is muted with it.

Two behaviours carry that weight and are pinned here directly:

A printer offline for three days is observed by 4,320 poll cycles and must
produce one message. And a jam somebody clears in two minutes must produce
none.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.notification import NotificationEvent, NotificationSettings, NotificationState
from app.notifications import conditions
from app.notifications.conditions import PRINTER_ERROR, clear, clear_missing, observe

START = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db_factory():
    """A session factory, for the tests that need two sessions at once to show
    what happens when two observers race."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_factory):
    async with db_factory() as session:
        yield session


def settings(**overrides) -> NotificationSettings:
    values = {
        "enabled": True,
        "settle_minutes": 10,
        "notify_printer_error": True,
    }
    values.update(overrides)
    return NotificationSettings(**values)


async def _events(db) -> int:
    return (await db.execute(select(func.count(NotificationEvent.id)))).scalar_one()


async def _see(db, cfg, *, at, key="printer.error:p1:media-jam"):
    return await observe(
        db,
        cfg,
        kind=PRINTER_ERROR,
        dedupe_key=key,
        title="Jam",
        body="Paper jam",
        now=at,
    )


# --- the two behaviours the feature stands on ------------------------------


@pytest.mark.asyncio
async def test_a_condition_seen_for_days_raises_exactly_one_event(db):
    """4,320 poll cycles over three days, one message."""
    cfg = settings()
    for minute in range(0, 3 * 24 * 60, 1):
        await _see(db, cfg, at=START + timedelta(minutes=minute))

    assert await _events(db) == 1


@pytest.mark.asyncio
async def test_a_jam_cleared_inside_the_settle_window_says_nothing(db):
    """Somebody walks over and clears it. The whole point of the delay: a
    feature that messages about this is one people mute."""
    cfg = settings()
    for minute in (0, 2, 4):
        await _see(db, cfg, at=START + timedelta(minutes=minute))
    await clear(db, "printer.error:p1:media-jam")

    assert await _events(db) == 0
    assert (await db.execute(select(func.count(NotificationState.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_condition_that_outlasts_the_window_is_raised_once(db):
    cfg = settings()
    assert await _see(db, cfg, at=START) is None
    assert await _see(db, cfg, at=START + timedelta(minutes=9)) is None

    raised = await _see(db, cfg, at=START + timedelta(minutes=10))
    assert raised is not None
    assert raised.title == "Jam"

    assert await _see(db, cfg, at=START + timedelta(minutes=11)) is None
    assert await _events(db) == 1


@pytest.mark.asyncio
async def test_the_same_condition_recurring_later_is_reported_again(db):
    """Clearing deletes the state row so the next occurrence is a fresh
    transition. A fault that comes back is news; if it were deduped against a
    stale row it would be silent forever."""
    cfg = settings()
    await _see(db, cfg, at=START)
    await _see(db, cfg, at=START + timedelta(minutes=15))
    await clear(db, "printer.error:p1:media-jam")

    later = START + timedelta(days=1)
    await _see(db, cfg, at=later)
    await _see(db, cfg, at=later + timedelta(minutes=15))

    assert await _events(db) == 2


# --- switches ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_is_raised_while_notifications_are_off(db):
    cfg = settings(enabled=False)
    for minute in (0, 15, 30):
        await _see(db, cfg, at=START + timedelta(minutes=minute))

    assert await _events(db) == 0


@pytest.mark.asyncio
async def test_a_single_trigger_can_be_silenced_without_the_others(db):
    """An admin who finds one trigger noisy silencing that one is a much better
    outcome than an admin silencing notifications."""
    cfg = settings(notify_printer_error=False)
    await _see(db, cfg, at=START)
    await _see(db, cfg, at=START + timedelta(minutes=30))

    assert await _events(db) == 0


@pytest.mark.asyncio
async def test_turning_a_trigger_back_on_does_not_fire_for_old_conditions(db):
    """A week-old condition is not news. Firing a burst of them on the first
    poll after re-enabling is exactly how somebody concludes the thing is
    broken and turns it off again."""
    off = settings(notify_printer_error=False)
    await _see(db, off, at=START)
    await _see(db, off, at=START + timedelta(days=7))

    on = settings(notify_printer_error=True)
    await _see(db, on, at=START + timedelta(days=7, minutes=1))
    await _see(db, on, at=START + timedelta(days=7, minutes=30))

    assert await _events(db) == 0


@pytest.mark.asyncio
async def test_a_settle_window_of_zero_raises_on_the_second_sighting(db):
    """Some sites will want it immediate. Still not the *first* sighting: the
    state row is the dedupe, and it has to exist before anything can be said
    about how long the condition has lasted."""
    cfg = settings(settle_minutes=0)
    assert await _see(db, cfg, at=START) is None
    assert await _see(db, cfg, at=START) is not None
    assert await _events(db) == 1


# --- distinctness and clearing ---------------------------------------------


@pytest.mark.asyncio
async def test_different_faults_on_one_printer_are_different_conditions(db):
    """A jam cleared this morning and a cover left open this afternoon are two
    things to be told about, not one."""
    cfg = settings()
    for key in ("printer.error:p1:media-jam", "printer.error:p1:cover-open"):
        await _see(db, cfg, at=START, key=key)
        await _see(db, cfg, at=START + timedelta(minutes=15), key=key)

    assert await _events(db) == 2


@pytest.mark.asyncio
async def test_conditions_not_observed_this_pass_are_cleared(db):
    """A caller reports only what is currently wrong, so something that stops
    being reported has to be cleared — otherwise the state row sticks around
    forever and the real recurrence is deduped away against it."""
    cfg = settings()
    for key in ("printer.error:p1:media-jam", "printer.error:p1:cover-open"):
        await _see(db, cfg, at=START, key=key)

    await clear_missing(db, prefix="printer.error:p1:", still_true={"printer.error:p1:media-jam"})
    await db.flush()

    remaining = (await db.execute(select(NotificationState.dedupe_key))).scalars().all()
    assert remaining == ["printer.error:p1:media-jam"]


@pytest.mark.asyncio
async def test_clearing_one_printer_does_not_touch_another(db):
    cfg = settings()
    await _see(db, cfg, at=START, key="printer.error:p1:media-jam")
    await _see(db, cfg, at=START, key="printer.error:p2:media-jam")

    await clear_missing(db, prefix="printer.error:p1:", still_true=set())
    await db.flush()

    remaining = (await db.execute(select(NotificationState.dedupe_key))).scalars().all()
    assert remaining == ["printer.error:p2:media-jam"]


# --- transactional coupling -------------------------------------------------


@pytest.mark.asyncio
async def test_a_rolled_back_observation_leaves_nothing(db):
    """Same property audit logging rests on. An alert about a status that never
    persisted would be a lie, and it is the caller's transaction — not this
    module — that decides."""
    cfg = settings()
    await _see(db, cfg, at=START)
    await _see(db, cfg, at=START + timedelta(minutes=15))
    await db.commit()
    assert await _events(db) == 1

    await _see(db, cfg, at=START + timedelta(hours=1), key="printer.error:p9:jam")
    await _see(db, cfg, at=START + timedelta(hours=2), key="printer.error:p9:jam")
    await db.rollback()

    assert await _events(db) == 1


@pytest.mark.asyncio
async def test_every_kind_has_a_settings_flag():
    """A kind with no flag would be unsilenceable, and getattr's default would
    quietly make it unsendable instead — either way somebody finds out the hard
    way."""
    kinds = {
        conditions.PRINTER_ATTENTION,
        conditions.PRINTER_ERROR,
        conditions.TONER_LOW,
        conditions.QUOTA_EXCEEDED,
    }
    assert set(conditions.ENABLED_FLAG) == kinds
    for flag in conditions.ENABLED_FLAG.values():
        assert hasattr(NotificationSettings, flag), f"{flag} is not a column"


@pytest.mark.asyncio
async def test_a_condition_with_no_polling_behind_it_can_raise_at_once(db):
    """Quota is the case. A printer is looked at every minute and a cartridge
    every half hour, so a second sighting is guaranteed; a quota is only ever
    observed when somebody submits a job. Waiting for a second sighting means
    the first person to hit their limit and then stop trying is never reported.

    There is also nothing to settle — a jam may clear itself, a quota does not.
    """
    cfg = settings(notify_quota_exceeded=True)
    raised = await observe(
        db,
        cfg,
        kind=conditions.QUOTA_EXCEEDED,
        dedupe_key="quota.exceeded:p1:someone@example.org:2026-09-01",
        title="Quota reached",
        body="200 of 200 pages",
        raise_immediately=True,
        now=START,
    )

    assert raised is not None
    assert await _events(db) == 1


@pytest.mark.asyncio
async def test_an_immediate_condition_still_only_raises_once(db):
    """Every subsequent job from the same person in the same period must be
    silent, or somebody at their limit generates a message per attempt."""
    cfg = settings(notify_quota_exceeded=True)
    for _ in range(5):
        await observe(
            db,
            cfg,
            kind=conditions.QUOTA_EXCEEDED,
            dedupe_key="quota.exceeded:p1:someone@example.org:2026-09-01",
            title="Quota reached",
            body="200 of 200 pages",
            raise_immediately=True,
            now=START,
        )

    assert await _events(db) == 1


@pytest.mark.asyncio
async def test_two_sessions_observing_the_same_condition_raise_once(db_factory):
    """Two jobs held on quota at the same moment, or a manual toner check
    overlapping the poll loop. The unique index stops a duplicate *state*;
    nothing stops a duplicate *event* except the compare-and-set, and
    duplicates are the failure people notice."""
    cfg = settings()
    key = "printer.error:p1:media-jam"

    async with db_factory() as first, db_factory() as second:
        for session in (first, second):
            await observe(
                session,
                cfg,
                kind=PRINTER_ERROR,
                dedupe_key=key,
                title="Jam",
                body="Paper jam",
                now=START,
            )
            await session.commit()

        later = START + timedelta(minutes=15)
        raised = []
        for session in (first, second):
            event = await observe(
                session,
                cfg,
                kind=PRINTER_ERROR,
                dedupe_key=key,
                title="Jam",
                body="Paper jam",
                now=later,
            )
            await session.commit()
            if event is not None:
                raised.append(event)

    assert len(raised) == 1
