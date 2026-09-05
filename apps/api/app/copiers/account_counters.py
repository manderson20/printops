"""Turns a copier's per-account counters into usage.

The device reports lifetime running totals per Account Track account, and
nothing else — there is no per-user job log on a bizhub
(docs/copier-capture-konica.md §3.6). So "how many pages did this person
copy this week" is not a question the copier can answer; it can only say
how many they have copied since the counter was last cleared. Usage is
therefore the difference between two readings, and this module is what
takes that difference.

Same computation as app/printers/counter_history.py does for SNMP printer
meters, and deliberately not shared with it: that one buckets one device's
counter by calendar day for a chart, this one attributes deltas to people
and writes them into the ledger. What they have in common is the one rule
that matters — a counter that goes *down* has been reset, not run
backwards.

What is deliberately NOT written here: the device's per-account *print*
counters. A print job reaches the copier from the print server, which
PrintOps already records as a Job row, so emitting those pages again as
copier usage would double-count every printed page in combined reporting
(app/reports/aggregation.py sums CopierUsageRecord.page_count as copies
regardless of activity_type). The print deltas stay in the reading rows,
where they remain auditable without polluting the totals.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.copiers.connector import DeviceAccountCounters
from app.copiers.registry import get_connector
from app.models.copier_account_counter import CopierAccountCounterReading
from app.models.copier_provisioning import CopierProvisionedAccount
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice

logger = logging.getLogger(__name__)

# Konica's color modes are disjoint page counts, not a hierarchy: a page
# is counted under exactly one of these or under Bw.
COLOR_TYPES = ("FullColor", "BiColor", "MonoColor")
MONO_TYPES = ("Bw",)

# Counter types that are not a count of pages produced, and would inflate
# any total that included them: the *Large types re-count large-format
# pages already counted above, and the rest are paper/original/duplex
# tallies kept for the device's own billing screens.
NON_PAGE_TYPES = ("BlackPrintPaper", "Paper", "Document", "DuplexTotal", "PrintPageTotal")


def _is_page_type(counter_type: str) -> bool:
    return not counter_type.endswith("Large") and counter_type not in NON_PAGE_TYPES


def _ensure_utc(dt: datetime) -> datetime:
    """Same reason as app/reports/untracked_copies.py:_ensure_utc — SQLite
    (tests) doesn't round-trip tzinfo through DateTime(timezone=True) the
    way Postgres does, and every comparison here is between a stored
    timestamp and a live one."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@dataclass
class AccountDelta:
    """What one account did between two readings."""

    account_id: str
    lists: dict[str, dict[str, int]] = field(default_factory=dict)
    follows_reset: bool = False

    def pages(self, activity: str, types: tuple[str, ...]) -> int:
        counters = self.lists.get(activity, {})
        return sum(counters.get(t, 0) for t in types)

    def total_pages(self, activity: str) -> int:
        """Every page-ish counter in one activity list. Uses the device's
        own Total when it reports one, rather than re-adding the parts —
        a model that reports both would otherwise double its own count."""
        counters = self.lists.get(activity, {})
        if "Total" in counters:
            return counters["Total"]
        return sum(count for name, count in counters.items() if _is_page_type(name))


def diff_counters(
    previous: dict[str, dict[str, int]], current: dict[str, dict[str, int]]
) -> AccountDelta:
    """Subtract two readings of the same account.

    A counter that came back lower than last time means the account's
    counters were cleared on the device (the Counter Clear button on the
    Account Track Counter page) or the account number was deleted and
    handed to someone else. In that case the current value *is* the usage
    since the clear, and the clear happened inside this window — so the
    reading itself is the delta. Dropping it instead would silently lose
    every page produced since the reset, which is the failure mode that
    matters here: a reset is rare, and losing a term's worth of usage
    because of one is not a good trade for tidier arithmetic."""
    reset = False
    for activity, counters in current.items():
        for name, value in counters.items():
            if value < previous.get(activity, {}).get(name, 0):
                reset = True
                break
        if reset:
            break

    lists: dict[str, dict[str, int]] = {}
    for activity, counters in current.items():
        deltas = {}
        for name, value in counters.items():
            before = 0 if reset else previous.get(activity, {}).get(name, 0)
            delta = value - before
            if delta:
                deltas[name] = delta
        if deltas:
            lists[activity] = deltas
    return AccountDelta(account_id="", lists=lists, follows_reset=reset)


@dataclass
class CounterPollResult:
    accounts_read: int = 0
    # First time this account has been read: the reading is stored as the
    # baseline and produces no usage, because there is nothing to subtract.
    baselines: int = 0
    unchanged: int = 0
    changed: int = 0
    usage_rows: int = 0
    # Of those usage rows, the ones recovered from a first reading of an
    # account PrintOps provisioned itself — pages made between provisioning
    # and the first poll, which are otherwise lost.
    baseline_usage_rows: int = 0
    resets: int = 0
    # Accounts on the device that PrintOps has no provisioning record for —
    # usage is still recorded, with no staff_email, and surfaces in the
    # Unmapped Activity flow.
    unmapped: int = 0

    @property
    def message(self) -> str:
        parts = [f"{self.accounts_read} accounts read"]
        if self.baselines:
            first = f"{self.baselines} first readings"
            if self.baseline_usage_rows:
                first += f" ({self.baseline_usage_rows} with usage since provisioning)"
            else:
                first += " (no usage yet)"
            parts.append(first)
        if self.usage_rows:
            parts.append(f"{self.usage_rows} usage rows from {self.changed} accounts")
        elif not self.baselines:
            parts.append("no new usage")
        if self.resets:
            parts.append(f"{self.resets} counters were cleared on the device")
        if self.unmapped:
            parts.append(f"{self.unmapped} not matched to a person")
        return ", ".join(parts)


async def _owner_map(
    db: AsyncSession, device: MfpDevice, at: datetime
) -> dict[str, CopierProvisionedAccount]:
    """device account number -> the person who held it at `at`.

    Resolved by date rather than by "the current row" so that an account
    number reused by a second person doesn't retroactively hand the first
    person's pages to them."""
    rows = (
        (
            await db.execute(
                select(CopierProvisionedAccount)
                .where(CopierProvisionedAccount.mfp_device_id == device.id)
                .order_by(CopierProvisionedAccount.provisioned_at)
            )
        )
        .scalars()
        .all()
    )
    owners: dict[str, CopierProvisionedAccount] = {}
    for row in rows:
        if _ensure_utc(row.provisioned_at) > at:
            continue
        if row.removed_at is not None and _ensure_utc(row.removed_at) <= at:
            continue
        owners[row.device_account_id] = row
    return owners


def _usage_rows(
    device: MfpDevice,
    delta: AccountDelta,
    owner: CopierProvisionedAccount | None,
    period_start: datetime,
    period_end: datetime,
) -> list[CopierUsageRecord]:
    """One row per walk-up activity that actually moved."""
    rows: list[CopierUsageRecord] = []

    def add(activity_type: str, pages: int, color: int | None, mono: int | None) -> None:
        rows.append(
            CopierUsageRecord(
                mfp_device_id=device.id,
                vendor=device.vendor,
                model=device.model,
                serial_number=device.serial_number,
                location_building=device.building,
                staff_email=owner.staff_email if owner else None,
                staff_employee_id=None,
                # The account number is what the device reported the usage
                # against, so it is what an admin sees on the copier and
                # what they would map by hand if it is unknown here.
                external_identity_used=delta.account_id,
                external_identity_type=owner.identity_type if owner else None,
                authentication_method="account_track",
                activity_type=activity_type,
                page_count=pages,
                color_page_count=color,
                monochrome_page_count=mono,
                period_start=period_start,
                period_end=period_end,
                source_connector=device.connector_type,
                # The whole delta, every activity and counter type, on
                # every row: the reason a number looks the way it does
                # should not require re-reading the device.
                raw_payload={
                    "counter_delta": delta.lists,
                    "staff_id": owner.identity_value if owner else None,
                    "follows_counter_reset": delta.follows_reset,
                },
            )
        )

    copy_pages = delta.total_pages("copy")
    if copy_pages:
        add(
            "copy",
            copy_pages,
            delta.pages("copy", COLOR_TYPES) or None,
            delta.pages("copy", MONO_TYPES) or None,
        )

    scan_fax = delta.lists.get("scan_fax", {})
    scanned = scan_fax.get("DocumentReadTotal", 0)
    if scanned:
        add("scan", scanned, None, None)
    fax_sent = scan_fax.get("FaxSend", 0)
    if fax_sent:
        add("fax", fax_sent, None, None)

    return rows


def _baseline_usage_rows(
    device: MfpDevice,
    account: DeviceAccountCounters,
    owner: CopierProvisionedAccount | None,
    now: datetime,
) -> list[CopierUsageRecord]:
    """Usage from an account's *first* reading — normally thrown away, and
    kept only when PrintOps put the account on the device itself.

    The default has to be to discard it. A copier that has been in service
    for years reports years of pages the first time it is read, and
    recording that would invent a day on which everyone copied their entire
    history.

    But an account PrintOps created started at zero, so its first reading
    is not history — it is everything that account has done since it was
    provisioned, which is usually the same day. Discarding that loses real
    pages by real people: it swallowed ten on the first copier this ran
    against, and would have swallowed a term's worth on a device polled
    monthly.

    The distinction is exactly whether a CopierProvisionedAccount row
    exists. No row means PrintOps did not create the account and knows
    nothing about where its counter started — discard. A row means we
    created it and the counter began at zero.

    One caveat, recorded on the row itself as from_provisioning_baseline:
    a sync that *rewrites* an existing account edits it in place and does
    not reset its counters, so a rewritten account adopted from an earlier
    system could carry pages made before PrintOps was involved. The flag is
    there so such a row can be found and discounted rather than silently
    trusted."""
    if owner is None:
        return []
    delta = AccountDelta(account_id=account.account_id, lists=account.lists)
    if not any(any(counters.values()) for counters in account.lists.values()):
        return []
    provisioned_at = _ensure_utc(owner.provisioned_at)
    rows = _usage_rows(device, delta, owner, provisioned_at, now)
    for row in rows:
        row.raw_payload["from_provisioning_baseline"] = True
    return rows


async def poll_account_counters(
    db: AsyncSession, device: MfpDevice, now: datetime | None = None
) -> CounterPollResult:
    """Read every account's counters, store them, and write whatever usage
    happened since the last read.

    Safe to run as often as you like: an account whose counters haven't
    moved costs an UPDATE of a timestamp and produces no usage row."""
    now = now or datetime.now(UTC)
    connector = get_connector(device.connector_type)
    snapshot: list[DeviceAccountCounters] = await connector.read_account_counters(device)

    existing = {
        reading.device_account_id: reading
        for reading in (
            (
                await db.execute(
                    select(CopierAccountCounterReading).where(
                        CopierAccountCounterReading.mfp_device_id == device.id,
                        CopierAccountCounterReading.superseded_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    owners = await _owner_map(db, device, now)

    result = CounterPollResult(accounts_read=len(snapshot))
    for account in snapshot:
        previous = existing.get(account.account_id)
        if previous is None:
            db.add(
                CopierAccountCounterReading(
                    mfp_device_id=device.id,
                    device_account_id=account.account_id,
                    counters=account.lists,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            result.baselines += 1
            owner = owners.get(account.account_id)
            for row in _baseline_usage_rows(device, account, owner, now):
                db.add(row)
                result.usage_rows += 1
                result.baseline_usage_rows += 1
            continue

        if previous.counters == account.lists:
            previous.last_seen_at = now
            result.unchanged += 1
            continue

        delta = diff_counters(previous.counters, account.lists)
        delta.account_id = account.account_id
        if delta.follows_reset:
            result.resets += 1
            logger.warning(
                "Account %s on %s reports lower counters than last read — treating the "
                "current values as usage since the counters were cleared.",
                account.account_id,
                device.name,
            )

        # The pages appeared between the last poll that saw the old values
        # and this one — not since the old values were first seen, which
        # could be weeks earlier.
        period_start = _ensure_utc(previous.last_seen_at)
        previous.superseded_at = now
        db.add(
            CopierAccountCounterReading(
                mfp_device_id=device.id,
                device_account_id=account.account_id,
                counters=account.lists,
                first_seen_at=now,
                last_seen_at=now,
                follows_counter_reset=delta.follows_reset,
            )
        )
        result.changed += 1

        owner = owners.get(account.account_id)
        if owner is None:
            result.unmapped += 1
        for row in _usage_rows(device, delta, owner, period_start, now):
            db.add(row)
            result.usage_rows += 1

    device.last_counter_poll_at = now
    device.last_counter_poll_ok = True
    device.last_counter_poll_message = result.message
    await db.commit()
    return result
