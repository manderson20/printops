"""Stage 4 of the copier accounting spec: connector *placeholders* for
Lexmark, HP, and Sharp — deliberately not full connectors. Per the
user's own spec: "Do not claim full support until the connector can
actually retrieve or import user-level accounting." None of these three
have a confirmed network API for per-user accounting or user/PIN
provisioning in this codebase, so none of them claim one. (Kyocera,
Ricoh, and Xerox were upgraded to real named-feature connectors — see
app/copiers/kyocera_department_management.py,
app/copiers/ricoh_user_code_auth.py, app/copiers/xerox_standard_accounting.py
— because each has a well-documented, stable, device-local accounting
feature. These three don't have an equivalent this codebase can
confidently name: HP's real solution (Access Control/JetAdvantage) and
Lexmark's (Print Management) are separate server products, not a device
toggle; Sharp's on-device mechanism varies too much across its OSA
platform generations to assert specifics.)

What each of these three actually does, uniformly:
- CSV import delegates to the same generic pipeline as every other
  connector — the only real integration path available today for any of
  them (export whatever accounting report the device offers, import it
  here).
- Meter reads reuse app/printers/snmp_counters.py's
  VENDOR_BREAKDOWN_FNS, falling back to the standard RFC 3805 total-only
  behavior for whichever of these vendors that dict has no entry for.
  Lexmark/HP already have entries there (each explicitly "unsupported"
  confidence — no real hardware has been available to verify a copy/print
  split for either, per that module's own docstring); Sharp has no
  vendor-specific entry at all yet, so it gets the same honest
  total-only fallback.
- get_capabilities is NOT implemented (raises CapabilityNotSupported) —
  none of these three has a vendor feature confirmed in this codebase
  yet. An admin can still set a device's capability flags manually
  (app/routers/mfp_devices.py's check-capabilities is just skipped for
  these).
- get_user_accounting/sync_users_to_device are NOT implemented — no
  vendor here has a confirmed network API for either.
"""

import asyncio

from app.copiers.connector import ConnectionTestResult, CopierConnector, MeterSnapshot
from app.copiers.generic_csv import GenericCsvConnector
from app.copiers.generic_snmp import DEFAULT_SNMP_COMMUNITY, DEFAULT_SNMP_PORT, DEFAULT_SNMP_VERSION
from app.core.crypto import decrypt
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice
from app.printers.snmp_counters import (
    SYS_DESCR_OID,
    VENDOR_BREAKDOWN_FNS,
    SnmpConfig,
    SnmpProbeError,
    VendorBreakdown,
    get_standard_total,
    snmp_get,
)


def _resolve_config(device: MfpDevice, vendor_profile: str) -> SnmpConfig:
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile=vendor_profile,
    )


def _test_connection_sync(device: MfpDevice, vendor_profile: str) -> ConnectionTestResult:
    if not device.ip_address:
        return ConnectionTestResult(ok=False, message="No IP address configured.")
    config = _resolve_config(device, vendor_profile)
    try:
        snmp_get(device.ip_address, SYS_DESCR_OID, config)
    except SnmpProbeError as exc:
        return ConnectionTestResult(ok=False, message=str(exc))
    return ConnectionTestResult(ok=True, message="SNMP responded.")


def _get_meter_snapshot_sync(device: MfpDevice, vendor_profile: str) -> MeterSnapshot:
    if not device.ip_address:
        raise SnmpProbeError("This device has no IP address configured for SNMP.")

    config = _resolve_config(device, vendor_profile)
    total = get_standard_total(device.ip_address, config)

    breakdown_fn = VENDOR_BREAKDOWN_FNS.get(vendor_profile, VENDOR_BREAKDOWN_FNS["generic"])
    try:
        breakdown: VendorBreakdown = breakdown_fn(device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,
        vendor_profile_used=vendor_profile,
    )


class _VendorPlaceholderMixin:
    """Shared behavior for every Stage 4 placeholder — see module
    docstring. Subclasses only set connector_type/display_name/
    setup_notes/_vendor_profile."""

    _vendor_profile: str

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device, self._vendor_profile)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device, self._vendor_profile)

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ):
        return await GenericCsvConnector().import_accounting_file(device, raw_bytes, template)


class LexmarkAccountingConnector(_VendorPlaceholderMixin, CopierConnector):
    connector_type = "lexmark_accounting"
    display_name = "Lexmark Accounting (placeholder)"
    _vendor_profile = "lexmark"
    setup_notes = (
        "Not yet a full integration — no confirmed network API for Lexmark's "
        "accounting features in PrintOps yet. Meter totals work over SNMP "
        "(RFC 3805 standard counter); a per-user copy/print breakdown isn't "
        "confirmed for Lexmark hardware. For per-user accounting, export "
        "whatever report Lexmark Print Management / the device's own admin "
        "page offers and import it via Accounting Imports."
    )


class HpAccessControlConnector(_VendorPlaceholderMixin, CopierConnector):
    connector_type = "hp_access_control"
    display_name = "HP Access Control (placeholder)"
    _vendor_profile = "hp"
    setup_notes = (
        "Not yet a full integration — HP Access Control / Secure Print's own "
        "accounting typically requires HP's separate JetAdvantage/Access "
        "Control server software, which PrintOps doesn't integrate with "
        "directly. Meter totals work over SNMP (RFC 3805 standard counter); "
        "the one real HP unit tested so far was a bare desktop printer with "
        "no copy function, so a copy/print breakdown isn't confirmed. For "
        "per-user accounting, export a report from HP's own management "
        "console and import it via Accounting Imports."
    )


class SharpAccountingConnector(_VendorPlaceholderMixin, CopierConnector):
    connector_type = "sharp_accounting"
    display_name = "Sharp Accounting (placeholder)"
    _vendor_profile = "sharp"
    setup_notes = (
        "Not yet a full integration — Sharp's on-device accounting mechanism "
        "and admin-page layout vary too much across its OSA platform "
        "generations for PrintOps to name a specific menu path with "
        "confidence; no Sharp hardware has been available to confirm an "
        "SNMP copy/print breakdown either, only the standard SNMP "
        "page-total counter. Check your specific model's own admin page for "
        "an Account/User counter export and bring that in via Accounting "
        "Imports."
    )
