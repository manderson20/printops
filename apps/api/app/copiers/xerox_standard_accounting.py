"""Xerox Standard Accounting (XSA) — same honest tier as
app/copiers/canon_department_id.py and konica_bizhub.py.

Xerox Standard Accounting is a well-documented, device-local feature on
ConnectKey/AltaLink/VersaLink devices (CentreWare Internet Services, the
device's own built-in admin page, under Accounting): staff enter a User
ID (and optionally an Account ID) at the device before copying/printing/
scanning, tracked locally on the device. Users/accounts are registered
directly on the device (or via a CSV Xerox itself supports importing on
some models) — PrintOps has no confirmed network API to push these
automatically, so provisioning stays unsupported here. The real
integration path for bringing usage into PrintOps is exporting XSA's own
accounting report from CentreWare IS and importing it (Accounting
Imports, same CSV pipeline as generic_csv).

What IS real here: meter reads use the standard RFC 3805 total (via
app/printers/snmp_counters.py:get_standard_total) — snmp_counters.py has
no Xerox-specific vendor MIB/breakdown built in this codebase yet, so no
copy/print split is claimed; total-only, "unsupported" confidence.
"""

import asyncio

from app.copiers.connector import (
    ConnectionTestResult,
    CopierConnector,
    DeviceCapabilityReport,
    MeterSnapshot,
)
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

SETUP_NOTES = (
    "On the device's touchscreen or CentreWare Internet Services (the "
    "device's own admin web page): Properties > Accounting > Xerox "
    "Standard Accounting. Turn it on and register each User ID (and "
    "optionally Account ID) there — Xerox has no confirmed network API for "
    "PrintOps to push these automatically (see this connector's own note "
    "on user provisioning). To bring usage into PrintOps: from CentreWare "
    "IS, export the XSA accounting report, then use Accounting Imports "
    "here to bring in the CSV. Map each staff member's User ID under Staff "
    "Copier Identities (identity type \"Vendor User ID\") and, if you use "
    "Account IDs too, a second row (identity type \"Department ID\") so "
    "the import can resolve either to a real person."
)


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile="xerox",
    )


def _test_connection_sync(device: MfpDevice) -> ConnectionTestResult:
    if not device.ip_address:
        return ConnectionTestResult(ok=False, message="No IP address configured.")
    config = _resolve_config(device)
    try:
        snmp_get(device.ip_address, SYS_DESCR_OID, config)
    except SnmpProbeError as exc:
        return ConnectionTestResult(ok=False, message=str(exc))
    return ConnectionTestResult(ok=True, message="SNMP responded.")


def _get_meter_snapshot_sync(device: MfpDevice) -> MeterSnapshot:
    if not device.ip_address:
        raise SnmpProbeError("This device has no IP address configured for SNMP.")

    config = _resolve_config(device)
    total = get_standard_total(device.ip_address, config)

    # No Xerox entry in VENDOR_BREAKDOWN_FNS — falls back to the generic
    # (total-only, unsupported-confidence) behavior, never a fabricated split.
    breakdown_fn = VENDOR_BREAKDOWN_FNS.get("xerox", VENDOR_BREAKDOWN_FNS["generic"])
    try:
        breakdown: VendorBreakdown = breakdown_fn(device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,
        vendor_profile_used="xerox",
    )


class XeroxStandardAccountingConnector(CopierConnector):
    connector_type = "xerox_standard_accounting"
    display_name = "Xerox Standard Accounting (XSA)"
    setup_notes = SETUP_NOTES

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        """Known facts about Xerox Standard Accounting (Xerox's own
        published documentation), not a live per-device probe — the
        router still records capabilities_source as "connector_reported"."""
        return DeviceCapabilityReport(
            capabilities={
                "walkup_copy_accounting": True,
                "department_id_accounting": True,  # Account ID
                "user_code_pin_auth": True,  # User ID
                "csv_accounting_export": True,
                "snmp_meter_counters": True,
                "api_accounting_retrieval": False,
                "remote_user_provisioning": False,
                "badge_card_auth": False,
                "ldap_auth": False,
            }
        )

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ):
        return await GenericCsvConnector().import_accounting_file(device, raw_bytes, template)
