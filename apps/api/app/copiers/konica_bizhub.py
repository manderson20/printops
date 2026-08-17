"""Konica Minolta bizhub — Stage 3 of the copier accounting spec. Same
honest scope and structure as app/copiers/canon_department_id.py:

Konica's own device-local access control (Account Track — group/department
level, or User Authentication — per-individual, both configured under
Utility > Administrator Settings > User Authentication/Account Track on
a real bizhub) has no network API PrintOps can call to pull per-user
accounting or push user/password provisioning. The real integration path
is exporting a Track Report / counter list from the device's own admin
web page (PageScope Web Connection) and importing it (Accounting
Imports, same CSV pipeline as generic_csv).

What IS real and verified here: the meter/connection checks reuse
app/printers/snmp_counters.py's Konica Minolta breakdown, confirmed live
against a real bizhub 750i (see that module's docstring) — forced to
vendor_profile="konica_minolta" instead of relying on sysDescr
auto-detection. Note the breakdown itself is only "best_effort" there
(which meter is copy vs. print isn't confirmed against an official MIB
or firmware doc, just a numerically consistent split) — this connector
doesn't upgrade that confidence, it just reports it honestly, same as
the underlying module already does.
"""

import asyncio

from app.copiers.connector import (
    CapabilityNotSupported,
    ConnectionTestResult,
    CopierConnector,
    DeviceCapabilityReport,
    DeviceUser,
    MeterSnapshot,
    SyncResult,
)
from app.copiers.device_admin import get_admin_credentials
from app.copiers.generic_csv import GenericCsvConnector
from app.copiers.generic_snmp import DEFAULT_SNMP_COMMUNITY, DEFAULT_SNMP_PORT, DEFAULT_SNMP_VERSION
from app.copiers.konica_admin import KonicaAdminError, KonicaAdminSession
from app.core.crypto import decrypt
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice
from app.models.staff_copier_identity import StaffCopierIdentity
from app.printers.snmp_counters import (
    SYS_DESCR_OID,
    VENDOR_BREAKDOWN_FNS,
    SnmpConfig,
    SnmpProbeError,
    VendorBreakdown,
    get_standard_total,
    snmp_get,
)

# Konica's published Account Track ceiling for i-Series. Used to stop a
# sync rather than letting the device reject the overflow one account at a
# time (see MfpDevice.provision_org_unit_paths for scoping a copier to its
# own building so this is not reached in the first place).
MAX_ACCOUNTS = 1000

SETUP_NOTES = (
    "On the device's touchscreen or PageScope Web Connection admin page: "
    "Utility > Administrator Settings > User Authentication/Account Track. "
    "Turn on Account Track (group/department-level tracking) or User "
    "Authentication (per-individual) and register each account/user there — "
    "Konica has no network API for PrintOps to push these automatically (see "
    "this connector's own note on user provisioning). To bring usage into "
    "PrintOps: from the same admin page, export the Track Report / counter "
    "list (PageScope Web Connection > Maintenance/Counter, or the device's "
    "own report-print function), then use Accounting Imports here to bring "
    "in the CSV. Map each staff member's Account Name/User Name under Staff "
    'Copier Identities (identity type "Department ID" for Account Track, '
    '"Vendor User ID" for User Authentication) so the import can resolve '
    "it to a real person."
)


def _resolve_config(device: MfpDevice) -> SnmpConfig:
    """Same shape as generic_snmp.py's own config builder, but
    vendor_profile is always forced to "konica_minolta" below — a device
    explicitly set up with this connector is already known to be Konica,
    so there's nothing to auto-detect."""
    return SnmpConfig(
        community=decrypt(device.snmp_community_encrypted)
        if device.snmp_community_encrypted
        else DEFAULT_SNMP_COMMUNITY,
        version=device.snmp_version or DEFAULT_SNMP_VERSION,
        port=device.snmp_port or DEFAULT_SNMP_PORT,
        vendor_profile="konica_minolta",
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
        breakdown: VendorBreakdown = VENDOR_BREAKDOWN_FNS["konica_minolta"](
            device.ip_address, config
        )
    except SnmpProbeError:
        breakdown = VendorBreakdown(copy=None, print=None, confidence="unsupported")

    return MeterSnapshot(
        total=total,
        copy=breakdown.copy,
        print=breakdown.print,
        confidence=breakdown.confidence,  # "best_effort" — never upgraded here
        vendor_profile_used="konica_minolta",
    )


class KonicaBizhubConnector(CopierConnector):
    connector_type = "konica_bizhub"
    display_name = "Konica Minolta bizhub"
    setup_notes = SETUP_NOTES

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        return await asyncio.to_thread(_test_connection_sync, device)

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot:
        return await asyncio.to_thread(_get_meter_snapshot_sync, device)

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        """Known facts about Account Track / User Authentication
        (Konica's own published documentation), not a live per-device
        probe — the router still records capabilities_source as
        "connector_reported", same as it would for an actual probe."""
        return DeviceCapabilityReport(
            capabilities={
                "walkup_copy_accounting": True,
                "department_id_accounting": True,  # Account Track
                "user_code_pin_auth": True,  # User Authentication
                "csv_accounting_export": True,
                "snmp_meter_counters": True,
                "api_accounting_retrieval": False,
                # Verified against real hardware — PrintOps registers
                # Account Track accounts directly (see sync_users_to_device
                # and docs/copier-capture-konica.md §3.9.2).
                "remote_user_provisioning": True,
                "badge_card_auth": False,
                "ldap_auth": False,
            }
        )

    async def list_device_users(self, device: MfpDevice) -> list[DeviceUser]:
        """Account Track accounts currently on the copier."""
        if not device.ip_address:
            raise CapabilityNotSupported("This copier has no IP address configured.")
        credentials = get_admin_credentials(device)
        async with KonicaAdminSession(device.ip_address, credentials) as session:
            try:
                accounts = await session.list_accounts()
            except KonicaAdminError as exc:
                if "AuthNotTrackMode" in str(exc):
                    raise CapabilityNotSupported(
                        "Account Track is switched off on this copier, so it has no "
                        "accounts to list."
                    ) from exc
                raise
        return [
            DeviceUser(
                identifier=a.track_id,
                name=a.name,
                has_password=a.has_password,
                disabled=a.account_stop,
            )
            for a in accounts
        ]

    async def sync_users_to_device(
        self, device: MfpDevice, identities: list[StaffCopierIdentity]
    ) -> SyncResult:
        """Register each staff identity as an Account Track account, with
        the 5-digit staff ID as the account password ("Password Only"
        mode) — see docs/copier-capture-konica.md §3.9.2.

        Additive only: accounts already on the device are left alone.
        The device never reveals an account's password
        (TrackPasswordExist is a boolean), so an existing account can't be
        diffed against the intended code — rewriting one blindly could
        change a working login for someone standing at the panel. Removing
        accounts is deliberately not done here either: the delete contract
        isn't captured yet, and silently deleting accounts is not something
        to guess at.

        Failures are per-account and counted, not fatal: one rejected ID
        shouldn't abandon the other 30. The device names the offending
        field on rejection, so the message says which account failed and
        why."""
        if not device.ip_address:
            raise CapabilityNotSupported("This copier has no IP address configured.")

        credentials = get_admin_credentials(device)

        async with KonicaAdminSession(device.ip_address, credentials) as session:
            try:
                existing = await session.list_accounts()
            except KonicaAdminError as exc:
                if "AuthNotTrackMode" in str(exc):
                    raise CapabilityNotSupported(
                        "Account Track is switched off on this copier. Turn it on "
                        "(User Auth/Account Track > Authentication Method, Account "
                        "Track = ON, method = Password Only) before syncing users."
                    ) from exc
                raise

            used_numbers = {account.track_id for account in existing}
            existing_count = len(existing)

            synced = 0
            failed = 0
            messages: list[str] = []
            next_number = 1

            for identity in identities:
                if existing_count + synced >= MAX_ACCOUNTS:
                    messages.append(
                        f"Stopped at the copier's {MAX_ACCOUNTS}-account limit; "
                        f"{len(identities) - synced - failed} not added."
                    )
                    break

                while str(next_number) in used_numbers:
                    next_number += 1
                if next_number > MAX_ACCOUNTS:
                    messages.append("No free account numbers left on this copier.")
                    break

                # 8 characters max, and it's what a person sees at the panel,
                # so prefer the local part of their email over a truncated
                # display name.
                label = (identity.staff_email or "").split("@")[0][:8] or str(next_number)
                try:
                    await session.create_account(
                        track_number=str(next_number),
                        name=label,
                        password=identity.identity_value,
                    )
                except KonicaAdminError as exc:
                    failed += 1
                    messages.append(f"{identity.staff_email}: {exc}")
                else:
                    used_numbers.add(str(next_number))
                    synced += 1

        summary = f"{synced} added, {failed} failed, {existing_count} already on the copier."
        if messages:
            summary = f"{summary} " + " ".join(messages[:5])
            if len(messages) > 5:
                summary += f" (+{len(messages) - 5} more)"
        return SyncResult(synced_count=synced, failed_count=failed, message=summary)

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ):
        # The Track Report / counter list is a plain CSV export (see
        # SETUP_NOTES) — same parsing pipeline as generic_csv. No
        # Konica-specific column quirks confirmed yet; flag it if a real
        # export's columns don't match what gets mapped here.
        return await GenericCsvConnector().import_accounting_file(device, raw_bytes, template)
