"""One person's own printing and copying as a list of line items.

The counterpart to the aggregate views in app/reports/aggregation.py:
those answer "how much", this answers "what, and when". It exists because
a page total is not a thing a person recognises — they remember printing
a handbook on Tuesday, and want to see that row.

The honest difficulty is that the two sources do not describe the same
kind of event, and this module refuses to pretend otherwise.

A print job is an instant: someone pressed print at 14:07 and CUPS
recorded it. A walk-up copy is not. A Konica reports one lifetime counter
per account that only climbs; PrintOps polls it and writes the difference
between two readings (app/copiers/account_counters.py). So a copy row
means "47 pages sometime between 14:23 and 20:23" — a window, not a
moment. Per-job copy data does not exist to be fetched: Konica's job
history WebAPI answers `Nack "Webapi not supported."` on this firmware
generation even with MFP.JobLog.Enable on (docs/copier-capture-konica.md
S3.6), so this is the finest grain the hardware can produce.

ActivityRow therefore carries `at` for prints and `window_start`/
`window_end` for copies, and never invents the other. A UI that showed a
copy at "14:23" would be stating something nobody measured.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copier_usage import CopierUsageRecord
from app.models.job import Job
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.reports.aggregation import (
    ReportFilters,
    _apply_copier_filters,
    _apply_filters,
    physical_sheets_used,
)


@dataclass
class ActivityRow:
    kind: str  # "print" | "copy"
    # What it was: a document name for a print, the copier's own name for
    # a copy — a counter delta has no document to name.
    label: str
    # The machine that did it.
    where: str
    activity_type: str  # print | copy | scan | fax
    pages: int
    sheets: int

    # A print happened at an instant; a copy covers a window. Exactly one
    # side of this is ever populated — see the module docstring.
    at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    # Print-only: the device reports one mode for the whole job.
    color_mode: str | None = None
    duplex: bool | None = None
    # Copy-only: a counter delta covers many copies, so colour arrives as
    # a split rather than a mode. Both can be 0 on a real row — the
    # device reported a total and no breakdown.
    color_pages: int | None = None
    mono_pages: int | None = None

    @property
    def sort_key(self) -> datetime:
        """Newest first, by when the activity finished. A copy sorts by
        the end of its window: that is the only moment the reading
        actually confirms, and sorting by the start would float a long
        window above prints that happened during it."""
        return self.at or self.window_end or self.window_start or datetime.min


async def get_print_rows(db: AsyncSession, filters: ReportFilters) -> list[ActivityRow]:
    """Forwarded jobs only, matching what every total in this feature
    counts. A failed or cancelled job is a queue question rather than a
    usage one, and including it here would make this list disagree with
    the page count sitting above it."""
    stmt = _apply_filters(
        select(
            Job.document_name,
            Job.page_count,
            Job.color_mode,
            Job.duplex,
            Job.created_at,
            Printer.name,
        ).where(Job.status == "forwarded"),
        filters,
    )
    rows = []
    for document_name, page_count, color_mode, duplex, created_at, printer_name in (
        await db.execute(stmt)
    ).all():
        pages = page_count or 0
        rows.append(
            ActivityRow(
                kind="print",
                label=document_name or "Untitled document",
                where=printer_name or "(deleted printer)",
                activity_type="print",
                pages=pages,
                sheets=physical_sheets_used(pages, duplex),
                at=created_at,
                color_mode=color_mode,
                duplex=duplex,
            )
        )
    return rows


async def get_copy_rows(db: AsyncSession, filters: ReportFilters) -> list[ActivityRow]:
    """One row per counter delta — the grain the polling actually
    produces. Rolling several deltas up into a daily total would discard
    the only timing information there is."""
    stmt = _apply_copier_filters(
        select(
            CopierUsageRecord.page_count,
            CopierUsageRecord.color_page_count,
            CopierUsageRecord.monochrome_page_count,
            CopierUsageRecord.activity_type,
            CopierUsageRecord.period_start,
            CopierUsageRecord.period_end,
            CopierUsageRecord.created_at,
            CopierUsageRecord.duplex,
            MfpDevice.name,
        ),
        filters,
    )
    rows = []
    for (
        page_count,
        color_pages,
        mono_pages,
        activity_type,
        period_start,
        period_end,
        created_at,
        duplex,
        device_name,
    ) in (await db.execute(stmt)).all():
        pages = page_count or 0
        name = device_name or "(deleted copier)"
        rows.append(
            ActivityRow(
                kind="copy",
                label=name,
                where=name,
                activity_type=activity_type or "copy",
                pages=pages,
                # duplex is null on every counter-derived row (a Konica has
                # no per-account duplex counter at all), so this counts
                # them as simplex — the same conservative rule
                # app/reports/formulas.py:copy_cost documents.
                sheets=physical_sheets_used(pages, duplex),
                # created_at is the fallback for a row whose source gave
                # no period at all, so a window always has two ends to
                # show rather than a blank.
                window_start=period_start or created_at,
                window_end=period_end or created_at,
                color_pages=color_pages,
                mono_pages=mono_pages,
            )
        )
    return rows


async def get_my_activity(
    db: AsyncSession, filters: ReportFilters, *, limit: int
) -> tuple[list[ActivityRow], int]:
    """Both sources merged, newest first, capped at `limit`.

    Returns the capped rows and the true total, so a page can say "showing
    50 of 213" rather than silently presenting a slice as the whole
    history.

    Merged in Python rather than by a UNION: the two tables agree on
    almost none of their columns, and a query that made them agree would
    have to null out or rename most of both. The row counts here are one
    person's activity in one period, not a fleet-wide scan.
    """
    rows = await get_print_rows(db, filters) + await get_copy_rows(db, filters)
    rows.sort(key=lambda row: row.sort_key, reverse=True)
    return rows[:limit], len(rows)


async def resolve_device_ids(db: AsyncSession) -> dict[UUID, str]:
    """Device id -> name, for callers that already hold ids."""
    return {
        device.id: device.name for device in (await db.execute(select(MfpDevice))).scalars().all()
    }
