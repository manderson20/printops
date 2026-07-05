"""Ricoh User Code Authentication — same honest tier as
app/copiers/canon_department_id.py and konica_bizhub.py.

Ricoh's MFP line has a well-documented, device-local access-control
feature (Web Image Monitor, the device's own built-in admin page, under
Device Management > User Management): "User Code Authentication" tracks
copy/print/scan/fax volumes by a numeric code entered at the panel — no
directory server needed, unlike Ricoh's other auth modes (Basic/Windows/
LDAP Authentication), which this connector deliberately does NOT claim
support for, since those require additional infrastructure this codebase
has no confirmed integration with. User Code Authentication has no
network API PrintOps can call to pull per-user accounting or push
code provisioning — the real integration path is exporting a User
Counter report from Web Image Monitor and importing it (Accounting
Imports, same CSV pipeline as generic_csv).

What IS real here: meter reads use the standard RFC 3805 total (via
app/printers/snmp_counters.py:get_standard_total) — snmp_counters.py has
no Ricoh-specific vendor MIB/breakdown built in this codebase yet, so no
copy/print split is claimed; total-only, "unsupported" confidence,
same honest fallback the generic connectors already use for an
unconfirmed vendor.
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
    "On the device's touchscreen or Web Image Monitor (the device's own "
    "admin web page): Device Management > User Management > User "
    "Authentication Management. Set User Code Authentication and register "
    "each code there — Ricoh has no network API for PrintOps to push these "
    "automatically (see this connector's own note on user provisioning). "
    "PrintOps does not integrate with Ricoh's Basic/Windows/LDAP "
    "Authentication modes, which need a directory server. To bring usage "
    "into PrintOps: from Web Image Monitor, export the User Counter report, "
    "then use Accounting Imports here to bring in the CSV. Map each staff "
    "member's user code under Staff Copier Identities (identity type "
    "\"Department ID\" or \"User Code\") so the import can resolve it to a "
    "real person."
)


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile="ricoh",
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

    # No Ricoh entry in VENDOR_BREAKDOWN_FNS — falls back to the generic
    # (total-only, unsupported-confidence) behavior, never a fabricated split.
    breakdown_fn = VENDOR_BREAKDOWN_FNS.get("ricoh", VENDOR_BREAKDOWN_FNS["generic"])
    try:
        breakdown: VendorBreakdown = breakdown_fn(device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,
        vendor_profile_used="ricoh",
    )


class RicohUserCodeAuthConnector(CopierConnector):
    connector_type = "ricoh_user_code_auth"
    display_name = "Ricoh User Code Authentication"
    setup_notes = SETUP_NOTES

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        """Known facts about User Code Authentication (Ricoh's own
        published documentation), not a live per-device probe — the
        router still records capabilities_source as "connector_reported"."""
        return DeviceCapabilityReport(
            capabilities={
                "walkup_copy_accounting": True,
                "department_id_accounting": True,
                "user_code_pin_auth": True,
                "csv_accounting_export": True,
                "snmp_meter_counters": True,
                "api_accounting_retrieval": False,
                "remote_user_provisioning": False,
                "badge_card_auth": False,
                "ldap_auth": False,  # deliberately not claimed — needs a directory server
            }
        )

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ):
        return await GenericCsvConnector().import_accounting_file(device, raw_bytes, template)
