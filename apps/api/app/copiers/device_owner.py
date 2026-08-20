"""Attributes a whole device's copies to one named person.

For the copier nobody logs in to. A desk machine with one regular user —
the IT department's colour copier, a nurse's office unit — has no Account
Track, no PIN, no per-user anything, so every attribution path in
PrintOps skips it: app/copiers/account_counters.py has no per-account
counters to read, and app/reports/untracked_copies.py deliberately
excludes any printer with a linked MfpDevice so that properly-tracked
copiers can't be double-counted. The result is that its copies land
nowhere at all. Naming an owner (MfpDevice.default_owner_email) is the
admin saying "there is only one person here, and it is this one".

The pages come from the *device's own* copy meter, read through the
linked Printer's SNMP counter history (app/printers/snmp_counters.py
records it; PrinterCounterReading stores it) — the same measured
page_count_copy the Untracked Copy Activity report calls "measured"
rather than "estimated". So this is a real meter reading assigned to a
person, not an estimate of how many copies they probably made. What is
assumed is only the *who*, and that is the admin's own assertion.

Two limits, both recorded on every row this writes rather than left for
someone to rediscover:

- **Only on a meter that reports copies separately.** A printer whose
  page_count_confidence is "unsupported" reports one combined total, and
  inferring copies from it means subtracting PrintOps' own print jobs —
  the estimate app/reports/untracked_copies.py already makes, org-wide
  and explicitly labelled as an estimate. Handing one person a number
  built that way, under a column that elsewhere means measurement, would
  misrepresent it. Such a device is refused with a reason instead.

- **Never on a copier that already tracks per-account.** The two count the
  same physical copies from different ends — Account Track attributes them
  to the people who actually made them, this attributes the whole meter to
  one person — so a device doing both would report every copy twice, and
  inflate that one person's cost with everyone else's copying. Per-account
  data is strictly better where it exists (it names real people), so this
  stands down rather than competing with it.

- **No colour split.** SNMP's copy meter is a single count; it does not
  say how many of those pages were colour. The rows are written with
  colour and mono both null, which prices them at the mono rate — the
  same conservative rule job_cost and compute_environmental_impact
  already apply to a page whose colour mode nobody reported. On a colour
  copier that understates cost, and is the honest direction to be wrong
  in.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copier_account_counter import CopierAccountCounterReading
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.models.snmp import PrinterCounterReading

logger = logging.getLogger(__name__)

# Printer.page_count_confidence values whose page_count_copy is a real
# vendor-reported copy counter — same tuple app/reports/untracked_copies.py
# calls MEASURED_CONFIDENCE, and for the same reason.
MEASURED_CONFIDENCE = ("verified", "best_effort")

# What these rows carry instead of an account number. There is no code at
# the glass to record — that is the entire premise — so this says so
# plainly rather than leaving the column empty and looking unresolved.
DEFAULT_OWNER_IDENTITY = "(device default owner)"
SOURCE_CONNECTOR = "device_default_owner"


@dataclass
class DefaultOwnerResult:
    """What one attribution run did, in terms an admin can act on."""

    attributed_pages: int = 0
    usage_rows: int = 0
    # True when this run only moved the watermark forward: an owner was
    # just named, or the meter was replaced. Either way no usage is
    # written, and that is correct rather than a failure.
    baselined: bool = False
    meter_reset: bool = False
    # Populated when nothing could be attributed and the admin needs to
    # know why — no owner, no linked printer, a meter that can't separate
    # copies, or simply no readings yet.
    skipped_reason: str | None = None

    @property
    def message(self) -> str:
        if self.skipped_reason:
            return self.skipped_reason
        if self.meter_reset:
            return (
                "the printer's meter reads lower than last time — it was reset or "
                "replaced, so counting restarts from here"
            )
        if self.baselined:
            return "counting copies for this owner from now on"
        if not self.attributed_pages:
            return "no new copies since the last read"
        return f"{self.attributed_pages} copies attributed"


async def _has_account_readings(db: AsyncSession, device: MfpDevice) -> bool:
    """Whether this copier has ever reported per-account counters, which is
    the durable evidence that app/copiers/account_counters.py is already
    attributing its copies — truer than the auto_poll_counters flag alone,
    which an admin can switch off without the readings (or the usage rows
    taken from them) going away."""
    return (
        await db.execute(
            select(CopierAccountCounterReading.id)
            .where(CopierAccountCounterReading.mfp_device_id == device.id)
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def _latest_reading(
    db: AsyncSession, printer_id, before: datetime | None = None
) -> PrinterCounterReading | None:
    stmt = select(PrinterCounterReading).where(
        PrinterCounterReading.printer_id == printer_id,
        PrinterCounterReading.page_count_copy.is_not(None),
    )
    if before is not None:
        stmt = stmt.where(PrinterCounterReading.recorded_at <= before)
    stmt = stmt.order_by(PrinterCounterReading.recorded_at.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _earliest_reading_after(
    db: AsyncSession, printer_id, after: datetime
) -> PrinterCounterReading | None:
    stmt = (
        select(PrinterCounterReading)
        .where(
            PrinterCounterReading.printer_id == printer_id,
            PrinterCounterReading.page_count_copy.is_not(None),
            PrinterCounterReading.recorded_at > after,
        )
        .order_by(PrinterCounterReading.recorded_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _usage_row(
    device: MfpDevice,
    owner_email: str,
    pages: int,
    period_start: datetime,
    period_end: datetime,
    base_value: int,
    current_value: int,
) -> CopierUsageRecord:
    return CopierUsageRecord(
        mfp_device_id=device.id,
        vendor=device.vendor,
        model=device.model,
        serial_number=device.serial_number,
        location_building=device.building,
        staff_email=owner_email,
        staff_employee_id=None,
        external_identity_used=DEFAULT_OWNER_IDENTITY,
        # Not one of the StaffCopierIdentity types on purpose: nobody
        # presented an identity. This says where the name came from, so a
        # row attributed by an admin's standing assertion can always be
        # told apart from one a person authenticated for.
        external_identity_type="device_default",
        authentication_method=None,
        activity_type="copy",
        page_count=pages,
        # Null colour split — see the module docstring. Left null rather
        # than guessed so the cost side applies its own documented rule
        # for unknown colour instead of inheriting a guess made here.
        color_page_count=None,
        monochrome_page_count=None,
        duplex=None,
        period_start=period_start,
        period_end=period_end,
        source_connector=SOURCE_CONNECTOR,
        raw_payload={
            "meter_from": base_value,
            "meter_to": current_value,
            "counter_field": "page_count_copy",
            # The admin's assertion is the whole basis for the name on
            # this row, so it is recorded as part of the evidence.
            "attributed_by": "device_default_owner",
        },
    )


async def attribute_device_copies(db: AsyncSession, device: MfpDevice) -> DefaultOwnerResult:
    """Write the copies metered since the last run as the owner's usage.

    Does not commit — the caller owns the transaction, matching
    app/copiers/provisioning.py and letting the hourly loop batch a whole
    fleet into one commit.

    Safe to run as often as you like: the watermark means a device whose
    meter hasn't moved produces nothing at all.

    Every timestamp it writes comes from a counter reading rather than
    from the clock, deliberately: the pages were produced between two
    readings, and stamping them with the moment this function happened to
    run would date a week of copying to whenever someone pressed the
    button.
    """
    if not device.default_owner_email:
        return DefaultOwnerResult(skipped_reason="no default owner set for this device")

    # Per-account tracking wins wherever it exists. Both settings are
    # accepted independently by the API and shown as separate cards in the
    # UI, so this configuration is reachable by an admin who turns on
    # counter polling and later names an owner (or the reverse) — and it
    # would silently double every copy on the device in the page and cost
    # reports. Checked against readings as well as the auto-poll flag,
    # since counters can also be polled by hand from the device page.
    if device.auto_poll_counters or await _has_account_readings(db, device):
        return DefaultOwnerResult(
            skipped_reason=(
                "this copier reports per-account counters, which already say who made "
                "each copy — a whole-device owner would count the same copies a second "
                "time, so it is not applied here"
            )
        )

    if device.printer_id is None:
        return DefaultOwnerResult(
            skipped_reason="this copier isn't linked to a printer, so PrintOps has no meter to read"
        )

    printer = await db.get(Printer, device.printer_id)
    if printer is None:
        return DefaultOwnerResult(skipped_reason="the linked printer no longer exists")
    if not printer.snmp_enabled:
        return DefaultOwnerResult(
            skipped_reason=f"SNMP is off for {printer.name}, so its copy meter is never read"
        )
    if printer.page_count_confidence not in MEASURED_CONFIDENCE:
        return DefaultOwnerResult(
            skipped_reason=(
                f"{printer.name} reports one combined page total, not a separate copy "
                "counter — copies can't be measured on this device"
            )
        )

    watermark = device.default_owner_attributed_through
    latest = await _latest_reading(db, printer.id)
    if latest is None:
        return DefaultOwnerResult(
            skipped_reason=(
                "no copy meter readings recorded yet — check back after the next SNMP poll"
            )
        )

    # No watermark means an owner was just named. Start counting from the
    # most recent reading: everything before it happened before anyone
    # said whose it was, and handing someone a device's entire lifetime
    # on the day they were named is the one outcome that would make this
    # number untrustworthy from the start.
    if watermark is None:
        device.default_owner_attributed_through = latest.recorded_at
        return DefaultOwnerResult(baselined=True)

    base = await _latest_reading(db, printer.id, before=watermark)
    if base is None:
        # The watermark predates every surviving reading — counter history
        # is purged on a retention schedule (app/main.py's purge loop), so
        # this is expected on a device left idle longer than retention.
        # The earliest reading still held becomes the baseline; the pages
        # between it and the purged history are genuinely unrecoverable
        # and are not invented here.
        base = await _earliest_reading_after(db, printer.id, watermark)
    if base is None or base.recorded_at >= latest.recorded_at:
        return DefaultOwnerResult()

    delta = latest.page_count_copy - base.page_count_copy
    if delta < 0:
        # Same rule as counter_history._field_delta: a monotonic meter
        # that went down was reset or the unit was swapped, not run
        # backwards. Re-baseline instead of attributing a negative or
        # treating the new absolute value as a day's copying.
        logger.warning(
            "Copy meter for %s went backwards (%s -> %s) — re-baselining the default owner "
            "attribution rather than attributing the difference.",
            device.name,
            base.page_count_copy,
            latest.page_count_copy,
        )
        device.default_owner_attributed_through = latest.recorded_at
        return DefaultOwnerResult(baselined=True, meter_reset=True)

    device.default_owner_attributed_through = latest.recorded_at
    if delta == 0:
        return DefaultOwnerResult()

    db.add(
        _usage_row(
            device,
            device.default_owner_email,
            delta,
            base.recorded_at,
            latest.recorded_at,
            base.page_count_copy,
            latest.page_count_copy,
        )
    )
    return DefaultOwnerResult(attributed_pages=delta, usage_rows=1)
