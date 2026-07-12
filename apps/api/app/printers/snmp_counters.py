"""Polls each printer's lifetime page/copy/print counters over SNMP —
see app/printers/status.py for the sibling IPP-reachability probe this
mirrors (same probe-then-mutate-in-place-without-committing convention).

Live-tested against this district's real fleet (Canon MF642C/643C/644C,
HP LaserJet M402n, Konica Minolta bizhub 750i) before writing this module
— the OIDs below are not guessed from vendor docs alone:

- Standard Printer MIB (RFC 3805) prtMarkerCounterTable gives a lifetime
  total on every device tested, regardless of vendor.
- Canon's private MIB is self-describing (paired STRING labels +
  Counter32 values) — matched by label text, not a hardcoded index,
  since indices can shift across Canon model families/firmware.
- Konica Minolta's private MIB has a confirmed total and a numerically
  consistent two-way split, but isn't self-describing like Canon's — so
  its breakdown ships as "best_effort", never "verified".
- Lexmark/Kyocera have no real hardware in this fleet to verify a
  breakdown against yet — they (and any unrecognized vendor) get
  total-only ("unsupported") until tested or built from an official MIB.

Shells out to the net-snmp CLI (`snmpget`/`snmpwalk`, `apt install snmp`
on the host) rather than adding a pysnmp dependency — matches the
existing codebase convention of shelling out to system tools (see
app/printers/test_print.py, app/printers/release.py) instead of
reimplementing a protocol client. `-On` forces numeric OIDs in output so
parsing never depends on which vendor MIB text files happen to be
installed on the server.

Out of scope for now (not partially implemented): scan/fax counters,
color/mono splits, and feeding these numbers into the cost/Insights
model. HP's marker-supplies MIB also exposed toner cartridge data in
testing, which could one day feed PrinterTonerCartridge — not attempted
here.
"""

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models.printer import Printer
from app.models.report import PrinterTonerCartridge, PrinterTonerReading
from app.models.snmp import PrinterCounterReading, SnmpDefaultsSettings

logger = logging.getLogger(__name__)

VendorProfile = Literal["canon", "konica_minolta", "hp", "lexmark", "kyocera", "generic"]
Confidence = Literal["verified", "best_effort", "unsupported"]

SNMP_TIMEOUT_SECONDS = 3
SNMP_RETRIES = 1

# RFC 3805 Printer MIB — prtMarkerCounterTable / prtMarkerLifeCount.
STANDARD_MARKER_LIFECOUNT_OID = "1.3.6.1.2.1.43.10.2.1.4"

# Canon private MIB — self-describing: index -> STRING label / Counter32 value.
CANON_LABEL_OID = "1.3.6.1.4.1.1602.1.11.2.1.1.2"
CANON_VALUE_OID = "1.3.6.1.4.1.1602.1.11.2.1.1.3"

# Konica Minolta private MIB — confirmed total + an unlabeled two-way split
# that sums to it (see module docstring; "best_effort" only, never "verified").
KONICA_TOTAL_OID = "1.3.6.1.4.1.18334.1.1.1.5.7.2.1.1.0"
KONICA_METER_A_OID = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.1"
KONICA_METER_B_OID = "1.3.6.1.4.1.18334.1.1.1.5.7.2.2.1.5.1.2"

# Standard MIB-II sysDescr — used as the primary vendor-detection signal at
# poll time (see get_sys_descr_vendor_profile's docstring for why this beats
# the DB-field heuristic in practice).
SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"

# RFC 3805 Printer MIB — prtMarkerSuppliesTable columns (standard, not
# vendor-private like the Canon/Konica OIDs above). See get_toner_supplies.
SUPPLIES_TYPE_OID = "1.3.6.1.2.1.43.11.1.1.5"
SUPPLIES_DESCRIPTION_OID = "1.3.6.1.2.1.43.11.1.1.6"
# prtMarkerSuppliesLevel / prtMarkerSuppliesMaxCapacity — same table, same
# index suffix as the two above. Negative values are this MIB's convention
# for "unknown"/"unmeasurable" (a printer can report it has *some* toner
# left without being able to quantify it) — never guessed at, just treated
# as no percentage available. See _compute_level_percent.
SUPPLIES_LEVEL_OID = "1.3.6.1.2.1.43.11.1.1.9"
SUPPLIES_MAX_CAPACITY_OID = "1.3.6.1.2.1.43.11.1.1.8"

# prtMarkerSuppliesType (RFC 3805) values that represent an actual
# toner/ink cartridge, as opposed to a waste bin, drum, fuser, staples,
# etc. — other(1)/unknown(2) excluded on purpose, only the specific
# cartridge-shaped types are kept: toner(3), inkCartridge(6), tonerCartridge(21).
_CARTRIDGE_SUPPLY_TYPES = {"3", "6", "21"}


class SnmpProbeError(Exception):
    pass


@dataclass
class SnmpConfig:
    community: str
    version: str  # "v1" | "v2c"
    port: int
    vendor_profile: VendorProfile


@dataclass
class VendorBreakdown:
    copy: int | None
    print: int | None
    confidence: Confidence


_VENDOR_HINTS: tuple[tuple[str, VendorProfile], ...] = (
    ("canon", "canon"),
    ("konica", "konica_minolta"),
    ("minolta", "konica_minolta"),
    ("bizhub", "konica_minolta"),
    ("hp ", "hp"),
    ("hewlett", "hp"),
    ("laserjet", "hp"),
    ("lexmark", "lexmark"),
    ("kyocera", "kyocera"),
    ("taskalfa", "kyocera"),
)


def _detect_vendor_profile_from_text(text: str) -> VendorProfile:
    haystack = text.lower()
    for hint, profile in _VENDOR_HINTS:
        if hint in haystack:
            return profile
    return "generic"


def detect_vendor_profile(printer: Printer) -> VendorProfile:
    """DB-only heuristic over manufacturer/model/capabilities make_model
    text — no SNMP involved. Used as an immediate default (e.g. a
    frontend hint before the printer's ever been polled) and as the
    fallback if the live sysDescr fetch below fails.

    Confirmed *unreliable on its own* against this district's real Canon
    unit: its IPP-discovered manufacturer is blank and model is
    "CNMF642C/643C/644C" — no "canon" substring anywhere — so this
    returns "generic" for it. get_sys_descr_vendor_profile is the signal
    that actually works for that device (SNMP sysDescr says "Canon
    MF642C/643C/644C /P" verbatim) and takes priority at real poll time;
    this function is the fallback, not the primary source of truth."""
    caps = printer.capabilities or {}
    haystack = " ".join(filter(None, [printer.manufacturer, printer.model, caps.get("make_model")]))
    return _detect_vendor_profile_from_text(haystack)


def get_sys_descr_vendor_profile(ip: str, config: SnmpConfig) -> VendorProfile | None:
    """Fetches the standard MIB-II sysDescr and runs the same text
    heuristic against it. Returns None (not "generic") if the SNMP call
    itself fails, so the caller can fall back to the DB-field heuristic
    instead of conflating "unreachable" with "vendor unrecognized."""
    try:
        raw = snmp_get(ip, SYS_DESCR_OID, config)
    except SnmpProbeError:
        return None
    profile = _detect_vendor_profile_from_text(_extract_string_value(raw) or "")
    return profile if profile != "generic" else None


def resolve_snmp_config(printer: Printer, defaults: SnmpDefaultsSettings) -> SnmpConfig:
    """Printer-level override wins field-by-field over the global default;
    decrypts whichever community is actually used. vendor_profile here is
    the manual override or the DB-field heuristic only — _poll_counters_sync
    refines it further via a live sysDescr fetch when there's no manual
    override, since that's the more reliable signal in practice (see
    get_sys_descr_vendor_profile)."""
    community_encrypted = printer.snmp_community_encrypted or defaults.community_encrypted
    return SnmpConfig(
        community=decrypt(community_encrypted) if community_encrypted else "public",
        version=printer.snmp_version or defaults.version,
        port=printer.snmp_port or defaults.port,
        vendor_profile=printer.snmp_vendor_profile or detect_vendor_profile(printer),
    )


def _run_snmp(argv: list[str]) -> str:
    timeout = SNMP_TIMEOUT_SECONDS * (SNMP_RETRIES + 1) + 2
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SnmpProbeError(
            "The `snmpget`/`snmpwalk` commands aren't available on the PrintOps server "
            "(install the `snmp` package)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SnmpProbeError("SNMP request timed out.") from exc

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        raise SnmpProbeError(reason or "snmp command exited with an error.")
    if not result.stdout.strip():
        raise SnmpProbeError("No response from device.")
    return result.stdout


def _base_argv(command: str, ip: str, oid: str, config: SnmpConfig) -> list[str]:
    return [
        command,
        f"-v{config.version.removeprefix('v')}",
        "-c",
        config.community,
        "-t",
        str(SNMP_TIMEOUT_SECONDS),
        "-r",
        str(SNMP_RETRIES),
        "-On",
        f"{ip}:{config.port}",
        oid,
    ]


def snmp_get(ip: str, oid: str, config: SnmpConfig) -> str:
    return _run_snmp(_base_argv("snmpget", ip, oid, config))


def snmp_walk(ip: str, oid: str, config: SnmpConfig) -> dict[str, str]:
    """Returns {index-suffix: raw value-line}, e.g. for a line
    ".1.3.6.1.4.1.1602.1.11.2.1.1.2.17 = STRING: \"Copy (Total 1)\"" the key
    is "17". Uses -On (numeric OIDs) so this never depends on which MIB
    text files happen to be installed on the server."""
    output = _run_snmp(_base_argv("snmpwalk", ip, oid, config))
    rows: dict[str, str] = {}
    prefix = f".{oid}."
    for line in output.splitlines():
        if " = " not in line:
            continue
        raw_oid, _, value = line.partition(" = ")
        raw_oid = raw_oid.strip()
        if not raw_oid.startswith(prefix):
            continue
        index = raw_oid[len(prefix) :]
        rows[index] = value.strip()
    return rows


def _extract_counter_value(raw: str) -> int | None:
    """'Counter32: 9026' -> 9026; 'INTEGER: 9026' -> 9026; 'No Such
    Instance currently exists at this OID' -> None."""
    if ":" not in raw:
        return None
    _, _, value = raw.partition(":")
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        return None


def _extract_string_value(raw: str) -> str | None:
    """'STRING: "Copy (Total 1)"' -> 'Copy (Total 1)'."""
    if ":" not in raw:
        return None
    _, _, value = raw.partition(":")
    return value.strip().strip('"')


def get_standard_total(ip: str, config: SnmpConfig) -> int:
    """Walks the standard Printer MIB marker-counter table and sums every
    row found — handles both the common single-marker case (confirmed on
    all 3 real devices tested) and any multi-marker/multi-engine device.
    Raises SnmpProbeError if the walk itself fails."""
    rows = snmp_walk(ip, STANDARD_MARKER_LIFECOUNT_OID, config)
    total = 0
    found = False
    for raw in rows.values():
        value = _extract_counter_value(raw)
        if value is not None:
            total += value
            found = True
    if not found:
        raise SnmpProbeError("Standard Printer MIB returned no usable counter.")
    return total


CartridgeColorGuess = Literal["black", "cyan", "magenta", "yellow"]

# Order matters: checked in sequence, first match wins — put multi-letter
# colors before anything that could false-positive on a shorter substring.
_COLOR_KEYWORDS: tuple[tuple[str, CartridgeColorGuess], ...] = (
    ("cyan", "cyan"),
    ("magenta", "magenta"),
    ("yellow", "yellow"),
    ("black", "black"),
)

_HIGH_CAPACITY_KEYWORDS = ("xl", "high yield", "high-yield", "high capacity", "high-capacity")


@dataclass
class DetectedSupply:
    """One cartridge-type row read from prtMarkerSuppliesTable. `color`
    and `high_capacity` are always a best-effort text guess parsed from
    `description` (see get_toner_supplies's docstring for why) — never
    treated as verified/confirmed, unlike this module's counter
    breakdowns, which were checked against real hardware before being
    trusted. `description` itself (the raw device-reported string) is
    always shown alongside the guess so an admin can judge it directly."""

    description: str
    color: CartridgeColorGuess | None
    high_capacity: bool | None
    # Current supply level as a 0-100 percentage, from prtMarkerSuppliesLevel
    # / prtMarkerSuppliesMaxCapacity — None when either is missing or
    # non-positive (the MIB's "unknown" convention), never a guess.
    level_percent: int | None
    # Best-effort orderable part number parsed from `description` — see
    # _guess_model_from_description. Defaults to None so existing call
    # sites (tests, callers that don't care about this) don't need to
    # specify it.
    guessed_model: str | None = None


def _guess_cartridge_color(description: str) -> CartridgeColorGuess | None:
    lower = description.lower()
    for keyword, color in _COLOR_KEYWORDS:
        if keyword in lower:
            return color
    return None


def _guess_high_capacity(description: str) -> bool | None:
    """None (not False) when the description doesn't mention capacity at
    all — vendors generally only bother naming it when it IS the
    high-yield variant, so silence isn't evidence of a standard-yield
    cartridge, just unknown. Only ever returns True or None, deliberately
    never a confident False."""
    lower = description.lower()
    if any(keyword in lower for keyword in _HIGH_CAPACITY_KEYWORDS):
        return True
    return None


def _guess_model_from_description(description: str) -> str | None:
    """Best-effort orderable part number, parsed from the same
    prtMarkerSuppliesDescription string color/high_capacity are already
    guessed from — confirmed live against this district's real fleet:

    - HP writes the real SKU as the last token, e.g. "Black Cartridge HP
      CF226A" -> "CF226A", "Yellow Cartridge HP W2112X" -> "W2112X".
    - Canon writes either "Cartridge NNN" (e.g. "Canon Cartridge 054
      Black Toner" -> "Canon 054") or a bare "CRGNNN" token (e.g. "Canon
      CRG052 Black Toner" -> "Canon CRG052") — both are real Canon
      cartridge-line numbers, just different firmware generations.
    - Konica Minolta ("Toner (Black)") and Lexmark ("Black Cartridge")
      write nothing extractable at all — None for those, not a guess.

    Only used by sync_toner_levels to prefill a cartridge's model field
    when it's still empty; never overwrites an admin-entered value."""
    lower = description.lower()
    if "hp" in lower:
        match = re.search(r"\bHP\s+([A-Za-z0-9]+)\s*$", description)
        if match:
            return match.group(1)
    if "canon" in lower:
        match = re.search(r"\bCartridge\s+(\d+)\b", description)
        if match:
            return f"Canon {match.group(1)}"
        match = re.search(r"\b(CRG\d+)\b", description, re.IGNORECASE)
        if match:
            return f"Canon {match.group(1)}"
    return None


def _compute_level_percent(level: int | None, max_capacity: int | None) -> int | None:
    """0-100, or None when a percentage can't be derived — either value is
    missing, or either is non-positive (this MIB's convention for "the
    printer can't quantify this," e.g. -2 for prtMarkerSuppliesLevel).
    Clamped to [0, 100] since some firmware has been seen to report a raw
    level slightly above its own stated max capacity."""
    if level is None or max_capacity is None or level < 0 or max_capacity <= 0:
        return None
    return max(0, min(100, round(level / max_capacity * 100)))


def get_toner_supplies(ip: str, config: SnmpConfig) -> list[DetectedSupply]:
    """Walks the standard Printer MIB supplies table (RFC 3805) for
    cartridge-type rows, parsing each one's device-reported description
    string for a color and a high-capacity ("XL"/"High Yield") hint —
    feeds PrinterTonerCartridge.detected_* (app/models/report.py) via
    POST /printers/{id}/toner-cartridges/detect.

    Standard MIB, so — unlike this module's Canon/Konica counter
    breakdowns — it should work across vendors in principle. But also
    unlike those, it has NOT been confirmed against this district's real
    fleet yet (see module docstring's "not attempted here" note this
    supersedes): whether a given vendor's firmware writes "XL"/"High
    Yield" into the description at all, versus just a bare part number,
    is unverified. That's why color/high_capacity are always a best-effort
    guess (DetectedSupply), never asserted as confirmed.

    Also walks prtMarkerSuppliesLevel/prtMarkerSuppliesMaxCapacity (same
    table) to compute a level percentage — best-effort like color/
    high_capacity: a device that doesn't report these at all (or reports
    them as "unknown") just gets level_percent=None for that row rather
    than failing the whole probe (see _compute_level_percent).

    Raises SnmpProbeError if the *type* walk fails or returns nothing
    recognizable as a cartridge — that one's core to the feature. The
    level/max-capacity walks are best-effort: a device that doesn't
    support those columns at all (raises, rather than just returning
    empty) still yields cartridge rows, just with level_percent=None."""
    types = snmp_walk(ip, SUPPLIES_TYPE_OID, config)
    descriptions = snmp_walk(ip, SUPPLIES_DESCRIPTION_OID, config)
    try:
        levels = snmp_walk(ip, SUPPLIES_LEVEL_OID, config)
        max_capacities = snmp_walk(ip, SUPPLIES_MAX_CAPACITY_OID, config)
    except SnmpProbeError:
        levels = {}
        max_capacities = {}

    supplies: list[DetectedSupply] = []
    for index, raw_type in types.items():
        type_value = _extract_counter_value(raw_type)
        if type_value is None or str(type_value) not in _CARTRIDGE_SUPPLY_TYPES:
            continue
        raw_description = descriptions.get(index)
        if raw_description is None:
            continue
        description = _extract_string_value(raw_description)
        if not description:
            continue
        level = _extract_counter_value(levels.get(index, ""))
        max_capacity = _extract_counter_value(max_capacities.get(index, ""))
        supplies.append(
            DetectedSupply(
                description=description,
                color=_guess_cartridge_color(description),
                high_capacity=_guess_high_capacity(description),
                level_percent=_compute_level_percent(level, max_capacity),
                guessed_model=_guess_model_from_description(description),
            )
        )
    if not supplies:
        raise SnmpProbeError("No toner/ink cartridge supplies reported by this device.")
    return supplies


async def sync_toner_levels(
    db: AsyncSession, printer: Printer, config: SnmpConfig
) -> list[DetectedSupply]:
    """Polls this printer's toner supplies and upserts each color-matched
    PrinterTonerCartridge row's detected_description/detected_high_capacity/
    detected_at/current_level_percent/level_checked_at — creating a
    cost=0/yield_pages=0 placeholder row for a detected color with none yet,
    same convention POST /printers/{id}/toner-cartridges/detect
    (app/routers/printers.py) already used before this became its shared
    implementation. Also the function the 30-minute SNMP counter poll loop
    (app/main.py) calls, so live level data stays fresh without an admin
    needing to click Detect.

    Also prefills row.model from supply.guessed_model (see
    _guess_model_from_description) whenever the row doesn't already have
    one — confirmed live that HP/Canon descriptions carry a real orderable
    part number, Konica Minolta/Lexmark don't (guessed_model is None for
    those, so this is a no-op). Only ever fills an empty field; an
    admin-entered model is never overwritten by a later poll.

    Also appends a PrinterTonerReading for each matched color where a real
    percentage was available (never for level_percent=None — an
    all-unknown history point would just be chart noise), feeding
    app/printers/toner_history.py's toner-level-over-time chart.

    Does not commit — caller owns the transaction, this file's existing
    convention (see _poll_counters_sync). Raises SnmpProbeError if the walk
    itself fails; returns the supplies that couldn't be matched to a color,
    for callers that want to surface them (the on-demand endpoint does, the
    background loop doesn't)."""
    supplies = await asyncio.to_thread(get_toner_supplies, printer.ip_address, config)

    existing = await db.execute(
        select(PrinterTonerCartridge).where(PrinterTonerCartridge.printer_id == printer.id)
    )
    rows_by_color = {row.color: row for row in existing.scalars().all()}

    now = datetime.now(UTC)
    unmatched: list[DetectedSupply] = []
    for supply in supplies:
        if supply.color is None:
            unmatched.append(supply)
            continue
        row = rows_by_color.get(supply.color)
        if row is None:
            row = PrinterTonerCartridge(
                printer_id=printer.id, color=supply.color, cost=0.0, yield_pages=0
            )
            db.add(row)
            rows_by_color[supply.color] = row
        row.detected_description = supply.description
        row.detected_high_capacity = supply.high_capacity
        row.detected_at = now
        row.current_level_percent = supply.level_percent
        row.level_checked_at = now
        if not row.model and supply.guessed_model:
            row.model = supply.guessed_model
        if supply.level_percent is not None:
            db.add(
                PrinterTonerReading(
                    printer_id=printer.id,
                    color=supply.color,
                    level_percent=supply.level_percent,
                    recorded_at=now,
                )
            )
    return unmatched


def _canon_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    labels = snmp_walk(ip, CANON_LABEL_OID, config)
    values = snmp_walk(ip, CANON_VALUE_OID, config)
    copy_count: int | None = None
    print_count: int | None = None
    for index, raw_label in labels.items():
        label = (_extract_string_value(raw_label) or "").lower()
        if "total" not in label:
            continue
        value = _extract_counter_value(values.get(index, ""))
        if value is None:
            continue
        if label.startswith("copy") and copy_count is None:
            copy_count = value
        elif label.startswith("print") and print_count is None:
            print_count = value
    return VendorBreakdown(copy=copy_count, print=print_count, confidence="verified")


def _konica_minolta_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    total = _extract_counter_value(snmp_get(ip, KONICA_TOTAL_OID, config))
    meter_a = _extract_counter_value(snmp_get(ip, KONICA_METER_A_OID, config))
    meter_b = _extract_counter_value(snmp_get(ip, KONICA_METER_B_OID, config))
    have_all = total is not None and meter_a is not None and meter_b is not None
    if have_all and meter_a + meter_b != total:
        logger.warning(
            "Konica Minolta SNMP counter split didn't sum to the total for %s "
            "(meter_a=%s, meter_b=%s, total=%s) — this firmware may not match "
            "the shape this was verified against.",
            ip,
            meter_a,
            meter_b,
            total,
        )
    # Unconfirmed which meter is copy vs print without a firmware/MIB-file
    # cross-check — see module docstring. Reported as best_effort either way.
    return VendorBreakdown(copy=meter_a, print=meter_b, confidence="best_effort")


def _unsupported_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    """hp/lexmark/kyocera/generic today. Each has its own name (rather than
    sharing one function silently) so a future contributor has an obvious
    place to add a real implementation once there's hardware to verify
    against or an official vendor MIB file to build from."""
    return VendorBreakdown(copy=None, print=None, confidence="unsupported")


def _hp_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    """The one real HP unit tested (LaserJet M402n) is a bare desktop
    printer with no copy function — total already equals print count, and
    there's no reliable signal here for "this HP is a bare printer" vs
    "this HP is an MFP with an unconfirmed OID." Total-only until a real
    HP MFP is available to test a breakdown against."""
    return _unsupported_breakdown(ip, config)


def _lexmark_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    """No Lexmark hardware in this fleet to verify an OID against yet."""
    return _unsupported_breakdown(ip, config)


def _kyocera_breakdown(ip: str, config: SnmpConfig) -> VendorBreakdown:
    """No Kyocera hardware in this fleet to verify an OID against yet."""
    return _unsupported_breakdown(ip, config)


VENDOR_BREAKDOWN_FNS = {
    "canon": _canon_breakdown,
    "konica_minolta": _konica_minolta_breakdown,
    "hp": _hp_breakdown,
    "lexmark": _lexmark_breakdown,
    "kyocera": _kyocera_breakdown,
    "generic": _unsupported_breakdown,
}


def _poll_counters_sync(printer: Printer, defaults: SnmpDefaultsSettings) -> bool:
    """Blocking SNMP calls — run via asyncio.to_thread by the async
    entrypoint below, same split as app/printers/release.py's
    submit_released_job (sync helper, async caller wraps it). Returns
    True only when this call performed a fresh successful read — callers
    use that (not just "page_count_error is None", which can't
    distinguish a fresh success from an old success whose error field was
    never touched this cycle) to decide whether to record a
    PrinterCounterReading (app/printers/counter_history.py)."""
    config = resolve_snmp_config(printer, defaults)
    now = datetime.now(UTC)
    try:
        printer.page_count_total = get_standard_total(printer.ip_address, config)
        printer.page_count_error = None
    except SnmpProbeError as exc:
        printer.page_count_error = str(exc)
        printer.page_count_checked_at = now
        return False  # leave last-known copy/print/total in place

    # Refine vendor_profile via a live sysDescr fetch — more reliable than
    # the DB-field heuristic in practice (see get_sys_descr_vendor_profile's
    # docstring). Only when there's no manual override to respect.
    if not printer.snmp_vendor_profile:
        sys_descr_profile = get_sys_descr_vendor_profile(printer.ip_address, config)
        if sys_descr_profile is not None:
            config.vendor_profile = sys_descr_profile

    breakdown_fn = VENDOR_BREAKDOWN_FNS.get(config.vendor_profile, _unsupported_breakdown)
    try:
        breakdown = breakdown_fn(printer.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    printer.page_count_copy = breakdown.copy
    printer.page_count_print = breakdown.print
    printer.page_count_confidence = breakdown.confidence
    printer.page_count_vendor_profile_used = config.vendor_profile
    printer.page_count_checked_at = now
    return True


async def refresh_printer_counters(printer: Printer, defaults: SnmpDefaultsSettings) -> bool:
    """Probes `printer` over SNMP and updates its page_count_* fields in
    place. Does not commit — caller owns the transaction (matches
    app/printers/status.py's refresh_printer_status convention). No-ops
    entirely if printer.snmp_enabled is False. Returns True only when a
    fresh successful read happened this call — see _poll_counters_sync's
    docstring for why callers need this rather than inferring success from
    field state."""
    if not printer.snmp_enabled:
        return False
    return await asyncio.to_thread(_poll_counters_sync, printer, defaults)


def record_reading(printer: Printer) -> PrinterCounterReading:
    """Builds a PrinterCounterReading snapshot from printer's current
    page_count_* fields — call only after refresh_printer_counters
    returns True (a fresh successful read). Pure construction; caller
    still owns db.add/commit, matching this module's existing
    caller-owns-the-transaction convention."""
    return PrinterCounterReading(
        printer_id=printer.id,
        recorded_at=printer.page_count_checked_at,
        page_count_total=printer.page_count_total,
        page_count_copy=printer.page_count_copy,
        page_count_print=printer.page_count_print,
        page_count_confidence=printer.page_count_confidence,
    )


async def get_or_create_snmp_defaults(db: AsyncSession) -> SnmpDefaultsSettings:
    """Same get-or-create-singleton pattern as every settings model in
    app/routers/settings.py (commit + refresh, not just flush — a GET-only
    caller still needs the seeded row to actually persist, not just be
    visible within the current transaction); lives here (not duplicated)
    since both app/main.py's poll loop and the settings/printers routers
    need it."""
    result = await db.execute(select(SnmpDefaultsSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = SnmpDefaultsSettings(community_encrypted=encrypt("public"))
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings
