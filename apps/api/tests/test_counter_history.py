import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.snmp import PrinterCounterReading
from app.printers.counter_history import _field_delta, get_daily_deltas


@pytest_asyncio.fixture
async def db_session():
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


def _reading(printer_id, recorded_at, total, copy=None, print_=None):
    return PrinterCounterReading(
        printer_id=printer_id,
        recorded_at=recorded_at,
        page_count_total=total,
        page_count_copy=copy,
        page_count_print=print_,
        page_count_confidence="verified" if copy is not None else "unsupported",
    )


class TestFieldDelta:
    def test_normal_positive_delta(self):
        assert _field_delta(150, 100, uuid.uuid4(), "total") == 50

    def test_missing_current_is_none(self):
        assert _field_delta(None, 100, uuid.uuid4(), "total") is None

    def test_missing_previous_is_none(self):
        assert _field_delta(150, None, uuid.uuid4(), "total") is None

    def test_negative_delta_treated_as_unavailable(self, caplog):
        result = _field_delta(50, 1100, uuid.uuid4(), "total")
        assert result is None
        assert "likely a" in caplog.text


class TestGetDailyDeltas:
    async def test_basic_day_over_day_deltas(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=10), 1000, 400, 600))
        db_session.add(_reading(printer_id, now - timedelta(days=2), 1100, 420, 680))
        db_session.add(_reading(printer_id, now, 1150, 435, 715))
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        by_day = {d.bucket_start: d for d in deltas}
        assert len(by_day) == 2  # the -10 day reading is outside the 7-day window

        day_minus_2 = min(by_day.keys())
        day_0 = max(by_day.keys())
        assert by_day[day_minus_2].total_delta == 100  # 1100 - 1000 (boundary)
        assert by_day[day_minus_2].copy_delta == 20
        assert by_day[day_minus_2].print_delta == 80
        assert by_day[day_0].total_delta == 50  # 1150 - 1100
        assert by_day[day_0].copy_delta == 15
        assert by_day[day_0].print_delta == 35

    async def test_day_with_no_reading_is_omitted_not_zero(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=10), 900))  # true boundary
        db_session.add(_reading(printer_id, now - timedelta(days=3), 1000))
        db_session.add(_reading(printer_id, now, 1100))
        # no reading in between — days -2 and -1 should simply not appear
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        bucket_starts = {d.bucket_start for d in deltas}
        assert len(bucket_starts) == 2
        for d in deltas:
            assert d.total_delta is not None  # both present days have a real baseline

    async def test_multiple_readings_same_day_uses_the_last_one(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=1), 1000))
        db_session.add(_reading(printer_id, now.replace(hour=1) if now.hour > 1 else now, 1200))
        db_session.add(_reading(printer_id, now, 1250))
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        today = max(d.bucket_start for d in deltas)
        today_delta = next(d for d in deltas if d.bucket_start == today)
        assert today_delta.total_delta == 250  # 1250 (last reading), not 1200

    async def test_negative_delta_from_counter_reset_is_none(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=1), 5000))
        db_session.add(_reading(printer_id, now, 50))  # meter replaced/reset
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        today = max(d.bucket_start for d in deltas)
        today_delta = next(d for d in deltas if d.bucket_start == today)
        assert today_delta.total_delta is None

    async def test_copy_print_none_while_total_available(self, db_session):
        """Unsupported-vendor readings have copy/print = None but total
        still populated — total_delta should compute while copy/print
        stay None, not crash."""
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=1), 1000, None, None))
        db_session.add(_reading(printer_id, now, 1100, None, None))
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        today = max(d.bucket_start for d in deltas)
        today_delta = next(d for d in deltas if d.bucket_start == today)
        assert today_delta.total_delta == 100
        assert today_delta.copy_delta is None
        assert today_delta.print_delta is None

    async def test_no_boundary_reading_leaves_first_day_null(self, db_session):
        """A printer whose SNMP polling just started has no reading
        before the window — the very first day in the window has
        nothing to diff against, so its delta is unavailable too."""
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now, 1000))
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)

        assert len(deltas) == 1
        assert deltas[0].total_delta is None

    async def test_readings_for_other_printers_are_ignored(self, db_session):
        printer_id = uuid.uuid4()
        other_printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, now - timedelta(days=1), 1000))
        db_session.add(_reading(printer_id, now, 1100))
        db_session.add(_reading(other_printer_id, now - timedelta(days=1), 9000))
        db_session.add(_reading(other_printer_id, now, 9999))
        await db_session.commit()

        deltas = await get_daily_deltas(db_session, printer_id, days=7)
        today = max(d.bucket_start for d in deltas)
        today_delta = next(d for d in deltas if d.bucket_start == today)
        assert today_delta.total_delta == 100  # not the other printer's 999
