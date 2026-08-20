"""Walk-up copier activity PrintOps *can* put a name to.

The deliberate counterpart to app/reports/untracked_copies.py. That module
estimates activity nobody can be held to — a device's own meter climbing
with no idea who was standing at it. This one reports the opposite: pages
read back from a copier's per-account counters
(app/copiers/account_counters.py) and resolved to a person through the
account PrintOps provisioned for them.

Both belong on the same screen, because the pair is the actual answer to
"how much copying happens here": what is attributed, what is not, and
therefore how much of the picture the accounting covers. A tracked number
on its own invites the assumption that it is the whole picture.

Unattributed rows are counted, never hidden. A copy against an account
PrintOps didn't provision is real activity on a tracked device, and
rolling it into "tracked" would overstate coverage while dropping it would
lose pages — so it gets its own number and its own resolution flow
(app/routers/copier_unmapped.py).
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
from app.reports.aggregation import ReportFilters, _apply_copier_filters


@dataclass
class TrackedCopyDeviceEntry:
    device_id: UUID
    device_name: str
    building: str | None
    copy_pages: int = 0
    scan_pages: int = 0
    fax_pages: int = 0
    people: int = 0
    unattributed_pages: int = 0


@dataclass
class TrackedCopySummary:
    copy_pages: int = 0
    scan_pages: int = 0
    fax_pages: int = 0
    # People with at least one attributed page in this window — the honest
    # denominator for "is anyone actually using their code".
    people: int = 0
    unattributed_pages: int = 0
    # Devices PrintOps has ever read per-account counters from. A copier
    # with accounting switched off contributes nothing here and should not
    # be counted as covered.
    devices_reporting: int = 0
    devices: list[TrackedCopyDeviceEntry] = field(default_factory=list)


async def get_tracked_copy_summary(db: AsyncSession, filters: ReportFilters) -> TrackedCopySummary:
    """Attributed walk-up activity in the filtered window, per device.

    Reuses _apply_copier_filters so this agrees with every other copier
    report on what start/end/building filtering means — a tracked total
    that disagreed with the leaderboard beside it would be worse than no
    total at all."""
    stmt = _apply_copier_filters(
        select(
            CopierUsageRecord.mfp_device_id,
            CopierUsageRecord.activity_type,
            func.sum(func.coalesce(CopierUsageRecord.page_count, 0)),
        ),
        filters,
    ).group_by(CopierUsageRecord.mfp_device_id, CopierUsageRecord.activity_type)
    rows = (await db.execute(stmt)).all()

    # Unattributed pages get their own pass rather than a conditional sum
    # in the query above: "pages nobody owns" and "pages per activity" are
    # different questions, and folding them together made the query say
    # neither clearly.
    unattributed_stmt = _apply_copier_filters(
        select(
            CopierUsageRecord.mfp_device_id,
            func.sum(func.coalesce(CopierUsageRecord.page_count, 0)),
        ).where(CopierUsageRecord.staff_email.is_(None)),
        filters,
    ).group_by(CopierUsageRecord.mfp_device_id)
    unattributed = {
        device_id: pages or 0 for device_id, pages in (await db.execute(unattributed_stmt)).all()
    }

    people_stmt = _apply_copier_filters(
        select(
            CopierUsageRecord.mfp_device_id,
            func.count(func.distinct(CopierUsageRecord.staff_email)),
        ).where(CopierUsageRecord.staff_email.is_not(None)),
        filters,
    ).group_by(CopierUsageRecord.mfp_device_id)
    people_by_device = {
        device_id: count for device_id, count in (await db.execute(people_stmt)).all()
    }

    devices = {
        device.id: device for device in (await db.execute(select(MfpDevice))).scalars().all()
    }

    entries: dict[UUID, TrackedCopyDeviceEntry] = {}
    for device_id, activity, pages in rows:
        device = devices.get(device_id)
        entry = entries.setdefault(
            device_id,
            TrackedCopyDeviceEntry(
                device_id=device_id,
                device_name=device.name if device else "(deleted copier)",
                building=device.building if device else None,
                people=people_by_device.get(device_id, 0),
                unattributed_pages=unattributed.get(device_id, 0),
            ),
        )
        pages = pages or 0
        if activity == "copy":
            entry.copy_pages += pages
        elif activity == "scan":
            entry.scan_pages += pages
        elif activity == "fax":
            entry.fax_pages += pages

    summary = TrackedCopySummary(
        devices=sorted(entries.values(), key=lambda e: e.copy_pages, reverse=True)
    )
    for entry in summary.devices:
        summary.copy_pages += entry.copy_pages
        summary.scan_pages += entry.scan_pages
        summary.fax_pages += entry.fax_pages
        summary.unattributed_pages += entry.unattributed_pages

    total_people_stmt = _apply_copier_filters(
        select(func.count(func.distinct(CopierUsageRecord.staff_email))).where(
            CopierUsageRecord.staff_email.is_not(None)
        ),
        filters,
    )
    summary.people = (await db.execute(total_people_stmt)).scalar_one() or 0

    summary.devices_reporting = (
        await db.execute(
            select(func.count(MfpDevice.id)).where(MfpDevice.last_counter_poll_at.is_not(None))
        )
    ).scalar_one() or 0

    return summary
