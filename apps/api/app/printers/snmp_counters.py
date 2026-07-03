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
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models.printer import Printer
from app.models.snmp import SnmpDefaultsSettings

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


def _poll_counters_sync(printer: Printer, defaults: SnmpDefaultsSettings) -> None:
    """Blocking SNMP calls — run via asyncio.to_thread by the async
    entrypoint below, same split as app/printers/release.py's
    submit_released_job (sync helper, async caller wraps it)."""
    config = resolve_snmp_config(printer, defaults)
    now = datetime.now(UTC)
    try:
        printer.page_count_total = get_standard_total(printer.ip_address, config)
        printer.page_count_error = None
    except SnmpProbeError as exc:
        printer.page_count_error = str(exc)
        printer.page_count_checked_at = now
        return  # leave last-known copy/print/total in place

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


async def refresh_printer_counters(printer: Printer, defaults: SnmpDefaultsSettings) -> None:
    """Probes `printer` over SNMP and updates its page_count_* fields in
    place. Does not commit — caller owns the transaction (matches
    app/printers/status.py's refresh_printer_status convention). No-ops
    entirely if printer.snmp_enabled is False."""
    if not printer.snmp_enabled:
        return
    await asyncio.to_thread(_poll_counters_sync, printer, defaults)


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
