"""Kyocera Department Management / User Login — same honest tier as
app/copiers/canon_department_id.py and konica_bizhub.py.

Kyocera's ECOSYS/TASKalfa line has a well-documented, device-local
access-control feature under System Menu > User Login/Job Accounting (or
remotely via Command Center RX, the device's own built-in web admin):
"Job Accounting" tracks by department/account code, "User Login" tracks
by individual user. Neither has a network API PrintOps can call to pull
per-user accounting or push user/PIN provisioning — the real integration
path is exporting a Job Accounting Report / user count list from Command
Center RX and importing it (Accounting Imports, same CSV pipeline as
generic_csv).

What IS real and verified here: meter reads reuse
app/printers/snmp_counters.py's existing "kyocera" vendor profile — but
that module is explicit that no real Kyocera hardware has been available
yet to verify a copy/print split, so it stays "unsupported" confidence
(total-only). This connector doesn't upgrade that; it just uses the
correct, real Kyocera feature names for setup guidance and capabilities
instead of generic placeholder language.
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
    "On the device's touchscreen or Command Center RX (the device's own "
    "admin web page): System Menu > User Login/Job Accounting. Turn on Job "
    "Accounting (department/account-code level tracking) or User Login "
    "(per-individual) and register each account/user there — Kyocera has no "
    "network API for PrintOps to push these automatically (see this "
    "connector's own note on user provisioning). To bring usage into "
    "PrintOps: from Command Center RX, export the Job Accounting Report / "
    "user count list, then use Accounting Imports here to bring in the CSV. "
    "Map each staff member's account code or login name under Staff Copier "
    'Identities (identity type "Department ID" for Job Accounting, '
    '"Vendor User ID" for User Login) so the import can resolve it to a '
    "real person."
)


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile="kyocera",
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

    try:
        breakdown: VendorBreakdown = VENDOR_BREAKDOWN_FNS["kyocera"](device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,  # "unsupported" until real hardware verifies a split
        vendor_profile_used="kyocera",
    )


class KyoceraDepartmentManagementConnector(CopierConnector):
    connector_type = "kyocera_department_management"
    display_name = "Kyocera Department Management / User Login"
    setup_notes = SETUP_NOTES

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        """Known facts about Job Accounting / User Login (Kyocera's own
        published documentation), not a live per-device probe — the
        router still records capabilities_source as "connector_reported"."""
        return DeviceCapabilityReport(
            capabilities={
                "walkup_copy_accounting": True,
                "department_id_accounting": True,  # Job Accounting
                "user_code_pin_auth": True,  # User Login
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
