"""Turns append-only PrinterTonerReading rows (app/printers/
snmp_counters.py:sync_toner_levels) into a per-day, per-color toner level
series — for a toner-level-over-time chart.

Deliberately separate from app/printers/counter_history.py despite both
bucketing "by calendar day": that module diffs a *monotonic cumulative
counter* between bucket boundaries (a day's "usage" = last reading minus
the previous day's last reading). Toner percentage isn't cumulative — a
day's value is just that day's last reading, independently per color, no
diffing needed.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import PrinterTonerReading


@dataclass
class DailyTonerLevel:
    bucket_start: date
    black: int | None
    cyan: int | None
    magenta: int | None
    yellow: int | None


def _last_reading_per_day_per_color(
    readings: list[PrinterTonerReading],
) -> dict[date, dict[str, int]]:
    """readings must be in ascending recorded_at order — later same-day
    readings for a given color overwrite earlier ones, leaving each day's
    last reading per color (independently; one color having a reading
    that day doesn't require the others to)."""
    by_day: dict[date, dict[str, int]] = {}
    for reading in readings:
        day = reading.recorded_at.date()
        by_day.setdefault(day, {})[reading.color] = reading.level_percent
    return by_day


async def get_daily_toner_levels(
    db: AsyncSession, printer_id: UUID, days: int
) -> list[DailyTonerLevel]:
    """Buckets PrinterTonerReading rows for `printer_id` by calendar day
    (UTC) and returns one point per day that has at least one reading
    within the last `days` days — a day with no readings at all is
    omitted entirely (not zeroed), same "missing means unknown, not zero"
    convention as get_daily_deltas."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)
    result = await db.execute(
        select(PrinterTonerReading)
        .where(
            PrinterTonerReading.printer_id == printer_id,
            PrinterTonerReading.recorded_at >= window_start,
            PrinterTonerReading.recorded_at < now,
        )
        .order_by(PrinterTonerReading.recorded_at.asc())
    )
    by_day = _last_reading_per_day_per_color(list(result.scalars().all()))
    return [
        DailyTonerLevel(
            bucket_start=day,
            black=colors.get("black"),
            cyan=colors.get("cyan"),
            magenta=colors.get("magenta"),
            yellow=colors.get("yellow"),
        )
        for day, colors in sorted(by_day.items())
    ]
