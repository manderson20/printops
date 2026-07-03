"""Turns append-only PrinterCounterReading rows
(app/printers/snmp_counters.py:record_reading) into daily usage deltas
for a per-printer usage-over-time chart.

Deliberately separate from app/reports/aggregation.py — that module sums
*additive* per-job facts into buckets (each Job contributes its own
page_count once); this module diffs a *monotonic cumulative counter*
between bucket boundaries (a day's "usage" = last reading of that day
minus last reading of the previous day), a different computation that
only shares the "bucket by calendar day" idea in name with
_bucket_key/get_timeline.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snmp import PrinterCounterReading

logger = logging.getLogger(__name__)


@dataclass
class DailyCounterDelta:
    bucket_start: date
    total_delta: int | None
    copy_delta: int | None
    print_delta: int | None


def _field_delta(
    current: int | None, previous: int | None, printer_id: UUID, field_name: str
) -> int | None:
    """Diffs two readings of the same monotonic counter field. None if
    either side is missing (no data yet to diff against), or if the
    delta is negative — a Counter32 wraparound or a printer meter being
    reset/replaced, not a real "negative pages produced" — logged as a
    warning rather than silently shown as a nonsensical number."""
    if current is None or previous is None:
        return None
    delta = current - previous
    if delta < 0:
        logger.warning(
            "Negative %s delta for printer %s (current=%s, previous=%s) — likely a "
            "counter reset/wraparound; treating this day's delta as unavailable.",
            field_name,
            printer_id,
            current,
            previous,
        )
        return None
    return delta


async def get_daily_deltas(
    db: AsyncSession, printer_id: UUID, days: int
) -> list[DailyCounterDelta]:
    """Buckets PrinterCounterReading rows for `printer_id` by calendar day
    (UTC, matching app/reports/aggregation.py's _bucket_key convention)
    and returns one delta per day that has a reading within the last
    `days` days, diffing each day's last reading against the previous
    day's — or a boundary reading fetched from just before the window,
    so the first day in the window gets a real delta instead of always
    being null. A day with no reading at all is omitted from the result
    entirely (not zero) — SNMP being unreachable for a day shouldn't
    look like "zero pages printed."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)

    boundary_result = await db.execute(
        select(PrinterCounterReading)
        .where(
            PrinterCounterReading.printer_id == printer_id,
            PrinterCounterReading.recorded_at < window_start,
        )
        .order_by(PrinterCounterReading.recorded_at.desc())
        .limit(1)
    )
    boundary = boundary_result.scalar_one_or_none()

    window_result = await db.execute(
        select(PrinterCounterReading)
        .where(
            PrinterCounterReading.printer_id == printer_id,
            PrinterCounterReading.recorded_at >= window_start,
        )
        .order_by(PrinterCounterReading.recorded_at.asc())
    )
    readings = window_result.scalars().all()

    # Later same-day readings overwrite earlier ones, leaving each day's
    # last reading — ascending order guarantees that.
    last_per_day: dict[date, PrinterCounterReading] = {}
    for reading in readings:
        last_per_day[reading.recorded_at.date()] = reading

    deltas: list[DailyCounterDelta] = []
    previous = boundary
    for day in sorted(last_per_day.keys()):
        current = last_per_day[day]
        deltas.append(
            DailyCounterDelta(
                bucket_start=day,
                total_delta=_field_delta(
                    current.page_count_total,
                    previous.page_count_total if previous else None,
                    printer_id,
                    "total",
                ),
                copy_delta=_field_delta(
                    current.page_count_copy,
                    previous.page_count_copy if previous else None,
                    printer_id,
                    "copy",
                ),
                print_delta=_field_delta(
                    current.page_count_print,
                    previous.page_count_print if previous else None,
                    printer_id,
                    "print",
                ),
            )
        )
        previous = current
    return deltas
