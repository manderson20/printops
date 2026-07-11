"""Page-quota resolution — period boundary math, usage lookup, and the
single hold_reason decision point called from app/routers/jobs.py:create_job.

Deliberately checks *historical* usage only, never the incoming job's own
page count: a job's page_count isn't known until it completes (CUPS reports
it after the real ipp backend runs), long after create_job has already
returned — see infra/cups/backends/printops. So a quota can only ever hold
someone once they're *already* at/over their limit, not pre-emptively based
on the size of the job that would push them over."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.models.printer import Printer
from app.models.quota import PrinterUserQuota, QuotaSettings
from app.models.release_bypass import PrinterReleaseBypass


async def get_or_create_quota_settings(db: AsyncSession) -> QuotaSettings:
    result = await db.execute(select(QuotaSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = QuotaSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month, day=1)


def period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Calendar-aligned [start, end) window containing `now`, in whatever
    tzinfo `now` carries (callers should pass a UTC-aware datetime, to match
    Job.created_at's DateTime(timezone=True) columns)."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "daily":
        return day_start, day_start + timedelta(days=1)
    if period == "weekly":
        week_start = day_start - timedelta(days=day_start.weekday())
        return week_start, week_start + timedelta(days=7)
    if period == "monthly":
        month_start = day_start.replace(day=1)
        return month_start, _add_months(month_start, 1)
    if period == "quarterly":
        quarter_start_month = (day_start.month - 1) // 3 * 3 + 1
        quarter_start = day_start.replace(month=quarter_start_month, day=1)
        return quarter_start, _add_months(quarter_start, 3)
    if period == "yearly":
        year_start = day_start.replace(month=1, day=1)
        return year_start, year_start.replace(year=year_start.year + 1)
    raise ValueError(f"Unknown quota period: {period!r}")


async def get_effective_quota(
    db: AsyncSession, printer_id: uuid.UUID, user_email: str | None
) -> PrinterUserQuota | None:
    """The specific (printer_id, user_email) row if one exists, else this
    printer's default/wildcard row (user_email IS NULL), else None
    (unlimited)."""
    if user_email is not None:
        result = await db.execute(
            select(PrinterUserQuota).where(
                PrinterUserQuota.printer_id == printer_id,
                PrinterUserQuota.user_email == user_email,
            )
        )
        quota = result.scalar_one_or_none()
        if quota is not None:
            return quota

    result = await db.execute(
        select(PrinterUserQuota).where(
            PrinterUserQuota.printer_id == printer_id,
            PrinterUserQuota.user_email.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_pages_used(
    db: AsyncSession, printer_id: uuid.UUID, user_email: str, start: datetime, end: datetime
) -> int:
    """Pages already used by this user at this printer within [start, end) —
    same func.sum(func.coalesce(Job.page_count, 0)) idiom as
    app/reports/aggregation.py:get_user_leaderboard. Jobs still in flight
    (page_count not yet known) contribute 0, same as everywhere else."""
    result = await db.execute(
        select(func.coalesce(func.sum(func.coalesce(Job.page_count, 0)), 0)).where(
            Job.printer_id == printer_id,
            Job.submitted_by == user_email,
            Job.created_at >= start,
            Job.created_at < end,
        )
    )
    return result.scalar_one()


async def has_release_bypass(db: AsyncSession, printer_id: uuid.UUID, user_email: str) -> bool:
    """Whether `user_email` skips the PIN-release hold at this printer
    specifically (PrinterReleaseBypass, app/models/release_bypass.py) —
    e.g. a secretary who sits next to the copier."""
    result = await db.execute(
        select(PrinterReleaseBypass).where(
            PrinterReleaseBypass.printer_id == printer_id,
            PrinterReleaseBypass.user_email == user_email,
        )
    )
    return result.scalar_one_or_none() is not None


async def resolve_hold_reason(
    db: AsyncSession, printer: Printer, submitted_by: str | None
) -> str | None:
    """The single decision point for whether a newly-created job should be
    held, and why — called once from create_job, before the CUPS backend
    script's spool/PATCH step (infra/cups/backends/printops) acts on it.
    "pin_release"/"follow_me" always win over "quota" when both would apply,
    since a release_required/follow_me_enabled printer's PIN kiosk already
    handles delivery. follow_me_enabled wins over release_required when a
    printer has both on, since it's the strictly more permissive routing —
    the job stays releasable at this same printer too, just also at any
    other follow_me_enabled one (app/routers/release.py). A bypassed user is
    treated exactly as if both flags were off for them — they still fall
    through to ordinary quota resolution below, rather than skipping every
    hold outright."""
    if printer.release_required or printer.follow_me_enabled:
        bypassed = submitted_by is not None and await has_release_bypass(
            db, printer.id, submitted_by
        )
        if not bypassed:
            return "follow_me" if printer.follow_me_enabled else "pin_release"

    if submitted_by is None:
        return None

    settings = await get_or_create_quota_settings(db)
    if not settings.enabled:
        return None

    quota = await get_effective_quota(db, printer.id, submitted_by)
    if quota is None:
        return None

    start, end = period_bounds(quota.period, datetime.now(UTC))
    used = await get_pages_used(db, printer.id, submitted_by, start, end)
    if used >= quota.page_limit:
        return "quota"
    return None
