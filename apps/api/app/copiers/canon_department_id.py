"""Canon Department ID Management — Stage 2 of the copier accounting
spec. Honest scope, matching the spec's own explicit hedge ("clearly show
when direct provisioning is not supported and manual setup/import is
required"):

Canon's Department ID Management is a device-local access-control feature
(Settings/Registration > Management Settings > User Management >
Department ID Management on a real imageRUNNER admin panel) — it has no
network API PrintOps can call to pull per-department accounting or push
user/PIN provisioning. The only real integration path is exporting the
Department ID counter report from the device's own admin web UI and
importing it (Accounting Imports, same CSV pipeline as generic_csv —
see import_accounting_file below).

What IS real and verified here: the meter/connection checks reuse
app/printers/snmp_counters.py's Canon breakdown, confirmed live against
this district's own Canon MF642C/643C/644C fleet (see that module's
docstring) — forced to vendor_profile="canon" instead of relying on
sysDescr auto-detection, since a device explicitly configured with this
connector is already known to be a Canon.

Note: this district's currently-registered Canon units (MF642C/643C/644C)
are imageCLASS small-office MFPs — Department ID Management is much more
commonly available on Canon's imageRUNNER (enterprise) line. This
connector targets the *feature*, not any specific model already on file;
it's meant for whichever Canon devices (current or future) actually have
Department ID Management turned on.
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


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    """Same shape as generic_snmp.py's own config builder, but vendor_profile
    is always forced to "canon" below — a device explicitly set up with
    this connector is already known to be Canon, so there's nothing to
    auto-detect."""
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile="canon",
    )

SETUP_NOTES = (
    "On the device's own touchscreen or admin web page: Settings/Registration "
    "> Preferences (Function Settings) > Management Settings > User Management "
    "> Department ID Management. Turn it on and register each Department ID + "
    "PIN there — Canon has no network API for PrintOps to push these "
    "automatically (see this connector's own note on user provisioning). To "
    "bring usage into PrintOps: from that same screen, export/print the "
    "Department ID counter report, then use Accounting Imports here to bring "
    "in the CSV. Map each staff member's Department ID under Staff Copier "
    "Identities (identity type \"Department ID\") so the import can resolve it "
    "to a real person."
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
        breakdown: VendorBreakdown = VENDOR_BREAKDOWN_FNS["canon"](device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,
        vendor_profile_used="canon",
    )


class CanonDepartmentIdConnector(CopierConnector):
    connector_type = "canon_department_id"
    display_name = "Canon Department ID Management"
    setup_notes = SETUP_NOTES

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        """Known facts about the Department ID Management feature itself
        (Canon's own published documentation), not a live per-device
        probe — the router still records capabilities_source as
        "connector_reported", same as it would for an actual probe."""
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
                "ldap_auth": False,
            }
        )

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ):
        # The Department ID counter report is a plain CSV export (see
        # SETUP_NOTES) — same parsing pipeline as generic_csv. No
        # Canon-specific column quirks confirmed yet; flag it if a real
        # export's columns don't match what gets mapped here.
        return await GenericCsvConnector().import_accounting_file(device, raw_bytes, template)
