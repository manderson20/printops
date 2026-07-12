import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.report import PrinterTonerReading
from app.printers.toner_history import get_daily_toner_levels


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


def _reading(printer_id, color, recorded_at, level_percent):
    return PrinterTonerReading(
        printer_id=printer_id, color=color, recorded_at=recorded_at, level_percent=level_percent
    )


class TestGetDailyTonerLevels:
    async def test_basic_multi_color_bucketing(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, "black", now, 80))
        db_session.add(_reading(printer_id, "cyan", now, 40))
        db_session.add(_reading(printer_id, "magenta", now, 55))
        db_session.add(_reading(printer_id, "yellow", now, 60))
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 1
        assert points[0].black == 80
        assert points[0].cyan == 40
        assert points[0].magenta == 55
        assert points[0].yellow == 60

    async def test_day_with_no_readings_is_omitted_not_zero(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, "black", now - timedelta(days=5), 90))
        db_session.add(_reading(printer_id, "black", now, 60))
        # nothing in between — those days should simply not appear
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 2

    async def test_multiple_readings_same_day_uses_the_last_one(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        early_today = now.replace(hour=1) if now.hour > 1 else now
        db_session.add(_reading(printer_id, "black", early_today, 70))
        db_session.add(_reading(printer_id, "black", now, 55))
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 1
        assert points[0].black == 55  # last reading of the day, not the first

    async def test_color_missing_that_day_stays_none_independently(self, db_session):
        """A day where only some colors got a reading shouldn't force the
        others to appear as anything but None — colors are bucketed
        independently, not as a single combined row."""
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, "black", now, 80))
        # no cyan/magenta/yellow reading today
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 1
        assert points[0].black == 80
        assert points[0].cyan is None
        assert points[0].magenta is None
        assert points[0].yellow is None

    async def test_readings_outside_window_are_excluded(self, db_session):
        printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, "black", now - timedelta(days=30), 95))
        db_session.add(_reading(printer_id, "black", now, 50))
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 1
        assert points[0].black == 50

    async def test_readings_for_other_printers_are_ignored(self, db_session):
        printer_id = uuid.uuid4()
        other_printer_id = uuid.uuid4()
        now = datetime.now(UTC)
        db_session.add(_reading(printer_id, "black", now, 50))
        db_session.add(_reading(other_printer_id, "black", now, 99))
        await db_session.commit()

        points = await get_daily_toner_levels(db_session, printer_id, days=7)

        assert len(points) == 1
        assert points[0].black == 50
