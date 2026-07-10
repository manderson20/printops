"""Generic SNMP meter connector — reuses app/printers/snmp_counters.py's
already device-agnostic low-level functions (they take an ip + SnmpConfig,
not a Printer) rather than duplicating OID tables/vendor-breakdown logic.
That module stays exactly as-is for Printer's own meter display; this is
simply a second caller of its reusable pieces, targeting MfpDevice fields
instead. Only test_connection/get_meter_snapshot are implemented — this
connector has no per-user identity concept at all, so it never produces
CopierUsageRecord rows (see MfpDevice/CopierUsageRecord docstrings)."""

import asyncio

from app.copiers.connector import ConnectionTestResult, CopierConnector, MeterSnapshot
from app.core.crypto import decrypt
from app.models.mfp_device import MfpDevice
from app.printers.snmp_counters import (
    SYS_DESCR_OID,
    VENDOR_BREAKDOWN_FNS,
    SnmpConfig,
    SnmpProbeError,
    VendorBreakdown,
    get_standard_total,
    get_sys_descr_vendor_profile,
    snmp_get,
)

DEFAULT_SNMP_PORT = 161
DEFAULT_SNMP_VERSION = "v2c"
DEFAULT_SNMP_COMMUNITY = "public"


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile=device.snmp_vendor_profile or "generic",
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

    if not device.snmp_vendor_profile:
        sys_descr_profile = get_sys_descr_vendor_profile(device.ip_address, config)
        if sys_descr_profile is not None:
            config.vendor_profile = sys_descr_profile

    breakdown_fn = VENDOR_BREAKDOWN_FNS.get(config.vendor_profile, VENDOR_BREAKDOWN_FNS["generic"])
    try:
        breakdown: VendorBreakdown = breakdown_fn(device.ip_address, config)
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,
        vendor_profile_used=config.vendor_profile,
    )


class GenericSnmpConnector(CopierConnector):
    connector_type = "generic_snmp"
    display_name = "Generic SNMP Meter"

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)
