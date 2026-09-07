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
from app.notifications import conditions as notify
from app.notifications.settings import get_or_create_notification_settings


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


async def _get_user_quota(
    db: AsyncSession, printer_id: uuid.UUID, user_email: str
) -> PrinterUserQuota | None:
    result = await db.execute(
        select(PrinterUserQuota).where(
            PrinterUserQuota.printer_id == printer_id,
            PrinterUserQuota.user_email == user_email,
        )
    )
    return result.scalar_one_or_none()


async def _get_blanket_quota(db: AsyncSession, printer_id: uuid.UUID) -> PrinterUserQuota | None:
    result = await db.execute(
        select(PrinterUserQuota).where(
            PrinterUserQuota.printer_id == printer_id,
            PrinterUserQuota.user_email.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_effective_quota(
    db: AsyncSession, printer: Printer, user_email: str | None
) -> PrinterUserQuota | None:
    """The row that actually caps `user_email` at this printer, or None if
    nothing does. Which rows count depends on printer.quota_mode
    (app/models/printer.py):

      include — only the user's own row. The printer's blanket row is
                ignored entirely, so a leftover one from a previous stint in
                exclude mode can't quietly start capping the whole school.
      exclude — the blanket row caps everyone, and the *presence* of a
                per-user row means "exempt" (returns None), whatever limit
                that row happens to carry. That's what lets an admin set one
                limit for everyone and let a handful of people out, without
                enumerating every single person who should be capped.

    A returned row can still carry page_limit=None (an exemption row read
    back in include mode); callers treat that as no cap — see
    resolve_hold_reason."""
    if printer.quota_mode == "exclude":
        if user_email is not None and await _get_user_quota(db, printer.id, user_email) is not None:
            return None  # exempt
        return await _get_blanket_quota(db, printer.id)

    if user_email is None:
        return None
    return await _get_user_quota(db, printer.id, user_email)


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
    hold outright.

    A printer that is offline holds too — see the comment below on why that
    belongs here rather than in cupsd's own queue."""
    if printer.release_required or printer.follow_me_enabled:
        bypassed = submitted_by is not None and await has_release_bypass(
            db, printer.id, submitted_by
        )
        if not bypassed:
            return "follow_me" if printer.follow_me_enabled else "pin_release"

    # A printer PrintOps cannot currently reach holds its work here rather
    # than in cupsd. CUPS does queue behind an absent printer, but only for
    # MaxJobTime — three hours by default, which quietly destroyed a teacher's
    # job sent at 5pm to a classroom printer switched off for the night
    # (2026-08-23, job 5058). Held here instead, the job waits as long as it
    # takes, shows up on the Held Jobs page while it waits, and is released
    # automatically the moment the printer answers again
    # (app/printers/offline_holds.py).
    #
    # Below release/follow-me, which are how a job gets delivered at all and
    # whose kiosk the person is standing at; above quota, because a job that
    # cannot reach its printer is not a quota decision.
    if printer.status == "offline":
        return "printer_offline"

    if submitted_by is None:
        return None

    settings = await get_or_create_quota_settings(db)
    if not settings.enabled:
        return None

    quota = await get_effective_quota(db, printer, submitted_by)
    if quota is None or quota.page_limit is None:
        return None

    start, end = period_bounds(quota.period, datetime.now(UTC))
    used = await get_pages_used(db, printer.id, submitted_by, start, end)
    if used >= quota.page_limit:
        # Keyed per person per printer per *period start*, so somebody at their
        # limit on the 3rd generates one message rather than one for every job
        # they try until the period rolls over. The period start is in the key
        # because the next period is a genuinely new situation.
        await notify.observe(
            db,
            await get_or_create_notification_settings(db),
            kind=notify.QUOTA_EXCEEDED,
            dedupe_key=f"{notify.QUOTA_EXCEEDED}:{printer.id}:{submitted_by}:{start.date()}",
            title=f"{submitted_by} has reached their print quota",
            body=(
                f"{used} of {quota.page_limit} pages on {printer.name} "
                f"for the {quota.period} period beginning {start.date()}. "
                f"Further jobs are being held."
            ),
            subject_type="user",
            subject_id=submitted_by,
            severity="info",
            # No polling path stands behind this one. A printer is looked at
            # every minute and a cartridge every half hour, so a second sighting
            # is guaranteed; a quota is only ever observed when somebody submits
            # a job. Waiting for a second sighting here means the first person to
            # hit their limit and then stop trying is never reported at all.
            #
            # Nor is there anything to settle: a jam might clear itself, a quota
            # does not.
            raise_immediately=True,
        )
        return "quota"
    return None
