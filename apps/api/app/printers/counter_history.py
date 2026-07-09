"""Turns append-only PrinterCounterReading rows
(app/printers/snmp_counters.py:record_reading) into daily usage deltas —
for a per-printer usage-over-time chart (get_daily_deltas), and for the
Untracked Copy Activity report (app/reports/untracked_copies.py:
get_daily_deltas_range), which needs an explicit date range instead of a
"last N days from now" window.

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


def _diff_readings(
    readings: list[PrinterCounterReading],
    boundary: PrinterCounterReading | None,
    printer_id: UUID,
) -> list[DailyCounterDelta]:
    """Shared by get_daily_deltas and get_daily_deltas_range: given a
    boundary reading (the last one before the window, or None) and every
    reading within the window (ascending order), returns one delta per
    day that has a reading — a day with no reading at all is omitted
    entirely (not zero), since SNMP being unreachable for a day shouldn't
    look like "zero pages produced"."""
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


async def get_daily_deltas_range(
    db: AsyncSession,
    printer_id: UUID,
    start: datetime,
    end: datetime,
    boundary_floor: datetime | None = None,
) -> list[DailyCounterDelta]:
    """Same diffing logic as get_daily_deltas, but an explicit [start, end)
    range instead of "last N days from now" — for callers (like the
    Untracked Copy Activity report) that need to align with an arbitrary,
    already-computed date range rather than a lookback window.

    boundary_floor additionally constrains which reading can serve as the
    diffing baseline for the first day in the window — without it, that
    baseline is simply the last reading before `start`, which could
    predate `start` by an arbitrary amount. The Untracked Copy Activity
    report passes its enabled_at here specifically so the very first
    tracked day's delta can never partially reflect activity from before
    the feature was turned on.

    When a printer's SNMP history predates enablement (the usual case),
    `start` and `boundary_floor` are different points in time and a
    boundary reading between them may or may not exist — if the poller
    genuinely missed that gap, leaving the day null (rather than
    guessing) is correct, and is exactly what `boundary is None` already
    means.

    But `get_untracked_copy_summary` computes `start = max(filters.start,
    enabled_at)` — so on the very day a report covers the feature's own
    enablement, `start` and `boundary_floor` collapse to the *same*
    timestamp, making "a reading before start, but not before
    boundary_floor" impossible to satisfy no matter what data exists —
    not a missed poll, a query with an empty range by construction.
    Falling back to null there would silently drop every reading from
    enablement through day's end, not just the pre-enablement portion —
    so specifically in that case (and only that case; a real gap between
    a floor and an earlier start still leaves the day null as before),
    the earliest in-window reading (the first one at/after enabled_at) is
    promoted to serve as the baseline itself, same as if polling had
    started fresh at that moment."""
    boundary_stmt = select(PrinterCounterReading).where(
        PrinterCounterReading.printer_id == printer_id,
        PrinterCounterReading.recorded_at < start,
    )
    if boundary_floor is not None:
        boundary_stmt = boundary_stmt.where(PrinterCounterReading.recorded_at >= boundary_floor)
    boundary_result = await db.execute(
        boundary_stmt.order_by(PrinterCounterReading.recorded_at.desc()).limit(1)
    )
    boundary = boundary_result.scalar_one_or_none()

    window_result = await db.execute(
        select(PrinterCounterReading)
        .where(
            PrinterCounterReading.printer_id == printer_id,
            PrinterCounterReading.recorded_at >= start,
            PrinterCounterReading.recorded_at < end,
        )
        .order_by(PrinterCounterReading.recorded_at.asc())
    )
    readings = list(window_result.scalars().all())
    # boundary_floor >= start means no reading could ever satisfy the
    # boundary query above, regardless of data — an empty range by
    # construction, not a real gap (see docstring).
    if boundary is None and boundary_floor is not None and boundary_floor >= start and readings:
        boundary = readings[0]
        readings = readings[1:]
    return _diff_readings(readings, boundary, printer_id)


async def get_daily_deltas(
    db: AsyncSession, printer_id: UUID, days: int
) -> list[DailyCounterDelta]:
    """Buckets PrinterCounterReading rows for `printer_id` by calendar day
    (UTC, matching app/reports/aggregation.py's _bucket_key convention)
    and returns one delta per day that has a reading within the last
    `days` days — see get_daily_deltas_range for the underlying logic."""
    now = datetime.now(UTC)
    window_start = now - timedelta(days=days)
    return await get_daily_deltas_range(db, printer_id, window_start, now)
