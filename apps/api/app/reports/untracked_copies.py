"""Untracked Copy Activity — an aggregate, org-level estimate of walk-up
copies PrintOps otherwise has no visibility into, built from SNMP counter
history (app/printers/counter_history.py) rather than Job or
CopierUsageRecord. Deliberately separate from app/reports/aggregation.py:
this is neither summing additive job facts nor diffing a single printer's
counters for a chart, it's a fleet-wide rollup of per-printer counter
diffs with its own inclusion rules (see get_untracked_copy_summary).

Two numbers, computed differently depending on what a given printer's SNMP
counters can actually tell us — see Printer.page_count_confidence:
- "verified"/"best_effort" printers report a real, vendor-broken-out copy
  counter (page_count_copy) — copy_delta is a direct measurement, not an
  inference.
- "unsupported" printers only report one combined total counter — for
  these, total_delta minus the pages PrintOps actually printed there that
  day is the best available estimate. Sound specifically because PrintOps
  is the *only* print path in this architecture (see
  app/printers/queue_sync.py and the IPP-proxy design) — an unexplained
  total-counter increase isn't a guess about some other print path.

Never counts a printer that also has a linked MfpDevice
(app/models/mfp_device.py) — once that device's walk-up activity is
properly tracked via CopierUsageRecord, it must drop out of this
SNMP-inferred number rather than double-count.

Never reaches back before UntrackedCopySettings.enabled_at — SNMP counter
history predates this feature; only compute from whenever an admin
actually turned it on (see the model's docstring,
app/models/untracked_copies.py)."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.models.untracked_copies import UntrackedCopySettings
from app.printers.counter_history import get_daily_deltas_range
from app.reports.aggregation import ReportFilters

MEASURED_CONFIDENCE = ("verified", "best_effort")
ESTIMATED_CONFIDENCE = "unsupported"


async def get_or_create_untracked_copy_settings(db: AsyncSession) -> UntrackedCopySettings:
    result = await db.execute(select(UntrackedCopySettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UntrackedCopySettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@dataclass
class UntrackedCopyPrinterEntry:
    printer_id: str
    printer_name: str
    measured_copies: int = 0
    estimated_untracked: int = 0


@dataclass
class UntrackedCopySummary:
    measured_copies: int = 0
    estimated_untracked: int = 0
    tracking_since: datetime | None = None
    printers: list[UntrackedCopyPrinterEntry] = field(default_factory=list)


async def _eligible_printers(db: AsyncSession, filters: ReportFilters) -> list[Printer]:
    """SNMP-enabled printers matching the report's building/department/
    printer_id filters, excluding any printer already tracked via a
    linked MfpDevice (see module docstring)."""
    linked_result = await db.execute(
        select(MfpDevice.printer_id).where(MfpDevice.printer_id.is_not(None))
    )
    linked_printer_ids = {row[0] for row in linked_result.all()}

    stmt = select(Printer).where(Printer.snmp_enabled.is_(True))
    if filters.building is not None:
        stmt = stmt.where(Printer.building == filters.building)
    if filters.department is not None:
        stmt = stmt.where(Printer.department == filters.department)
    if filters.printer_id is not None:
        stmt = stmt.where(Printer.id == filters.printer_id)

    result = await db.execute(stmt)
    return [p for p in result.scalars().all() if p.id not in linked_printer_ids]


async def _daily_print_pages(
    db: AsyncSession, printer_id, start: datetime, end: datetime
) -> dict[date, int]:
    """Pages PrintOps actually printed at this printer, bucketed by
    calendar day (UTC) — fetched as raw rows and bucketed in Python
    rather than a SQL date-trunc, same reasoning app/reports/aggregation.py's
    module docstring gives for get_timeline (SQLite/Postgres don't agree
    on date-truncation syntax)."""
    result = await db.execute(
        select(Job.created_at, Job.page_count).where(
            Job.printer_id == printer_id,
            Job.created_at >= start,
            Job.created_at < end,
        )
    )
    pages_by_day: dict[date, int] = defaultdict(int)
    for created_at, page_count in result.all():
        pages_by_day[created_at.date()] += page_count or 0
    return pages_by_day


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """SQLite (used in tests) doesn't reliably round-trip tzinfo through a
    DateTime(timezone=True) column the way Postgres (production) does —
    normalize to UTC-aware so the Python-level max()/comparison below
    never mixes a naive and an aware datetime regardless of backend."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def get_untracked_copy_summary(
    db: AsyncSession, filters: ReportFilters
) -> UntrackedCopySummary:
    settings = await get_or_create_untracked_copy_settings(db)
    if not settings.enabled or settings.enabled_at is None:
        return UntrackedCopySummary()

    enabled_at = _ensure_utc(settings.enabled_at)
    filter_start = _ensure_utc(filters.start)
    window_start = max(filter_start, enabled_at) if filter_start else enabled_at
    window_end = _ensure_utc(filters.end) or datetime.now(UTC)
    if window_start >= window_end:
        return UntrackedCopySummary(tracking_since=enabled_at)

    summary = UntrackedCopySummary(tracking_since=enabled_at)
    for printer in await _eligible_printers(db, filters):
        if printer.page_count_confidence not in (*MEASURED_CONFIDENCE, ESTIMATED_CONFIDENCE):
            continue  # no copy-relevant SNMP data at all -- nothing to report

        deltas = await get_daily_deltas_range(
            db, printer.id, window_start, window_end, boundary_floor=enabled_at
        )
        entry = UntrackedCopyPrinterEntry(printer_id=str(printer.id), printer_name=printer.name)

        if printer.page_count_confidence in MEASURED_CONFIDENCE:
            entry.measured_copies = sum(d.copy_delta or 0 for d in deltas)
        elif printer.page_count_confidence == ESTIMATED_CONFIDENCE:
            printed_by_day = await _daily_print_pages(db, printer.id, window_start, window_end)
            for d in deltas:
                if d.total_delta is None:
                    continue
                printed = printed_by_day.get(d.bucket_start, 0)
                entry.estimated_untracked += max(d.total_delta - printed, 0)

        summary.measured_copies += entry.measured_copies
        summary.estimated_untracked += entry.estimated_untracked
        if entry.measured_copies or entry.estimated_untracked:
            summary.printers.append(entry)

    summary.printers.sort(
        key=lambda e: e.measured_copies + e.estimated_untracked, reverse=True
    )
    return summary
