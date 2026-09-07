"""Loading an organisation's calendar out of the database.

`app/reports/periods.py` is deliberately pure — it takes a calendar, terms and
a date, and knows nothing about sessions. This is the thin layer that fetches
those three things, so both the report endpoints and the settings editor
resolve periods from the same rows rather than each assembling their own view
of the calendar and drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reporting_period import (
    ReportingCalendar,
    ReportingTerm,
    ReportingTermInstance,
)
from app.reports.periods import CalendarSpec, TermOverride, TermSpec
from app.server_settings.service import get_or_create_server_settings


@dataclass(frozen=True)
class PeriodContext:
    """Everything `periods.resolve` needs, fetched once per request.

    Held together rather than passed as three arguments because every caller
    needs all three, and a call site that fetched the calendar but forgot the
    terms would silently resolve "this semester" to the whole year.
    """

    calendar: CalendarSpec
    terms: list[TermSpec]
    overrides: list[TermOverride]
    zone: ZoneInfo
    # Resolved in the district's zone, not the server's and not the caller's.
    # Carried here so every screen that asks "which period is it now" gets one
    # answer: the reports page and the settings preview disagreeing about the
    # current year is exactly the bug a hardcoded second calendar caused once
    # already.
    today: date


async def load_period_context(db: AsyncSession) -> PeriodContext:
    """The organisation's calendar, or the neutral default if it has none.

    An installation that has never opened the settings page has no calendar
    row. That is not an error and does not warrant creating one as a side
    effect of reading a report — `CalendarSpec()` is the same July-start,
    "Year", no-terms default the schema declares.
    """
    server = await get_or_create_server_settings(db)
    try:
        zone = ZoneInfo(server.timezone)
    except Exception:
        # A zone dropped by a tzdata update. Reports a few hours out beat
        # reports that 500, and the settings page still shows what is set —
        # the same trade _district_zone makes.
        zone = ZoneInfo("UTC")

    calendar_row = (await db.execute(select(ReportingCalendar).limit(1))).scalar_one_or_none()

    term_rows = (
        (await db.execute(select(ReportingTerm).order_by(ReportingTerm.position))).scalars().all()
    )

    instance_rows = (await db.execute(select(ReportingTermInstance))).scalars().all()

    return PeriodContext(
        zone=zone,
        today=datetime.now(zone).date(),
        calendar=CalendarSpec.from_row(calendar_row),
        terms=[
            TermSpec(
                name=row.name,
                start_month=row.start_month,
                start_day=row.start_day,
                position=row.position,
            )
            for row in term_rows
        ],
        overrides=[
            TermOverride(
                reporting_year=row.reporting_year,
                name=row.name,
                start=row.start_date,
                end=row.end_date,
                position=row.position,
            )
            for row in instance_rows
        ],
    )
