"""Vendor-neutral interface every copier accounting connector implements.

Core principle (the user's own framing): build the accounting core
vendor-neutral, keep the messy vendor-specific parts inside connectors.
There is no universal cross-vendor protocol for user-level copy
accounting, so this is a capability/connector system rather than a single
implementation — see app/models/mfp_device.py's cap_* fields for the
per-device capability profile that gates which of these a router will
actually call.

Every method defaults to raising CapabilityNotSupported rather than being
a strict @abstractmethod — a concrete connector overrides only what it
actually does, and never fakes support it doesn't have (same philosophy
as app/printers/snmp_counters.py's verified/best_effort/unsupported
confidence levels, just applied to whole methods instead of one field).
"""

from abc import ABC
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice
from app.models.staff_copier_identity import StaffCopierIdentity
from app.printers.snmp_counters import SnmpProbeError


class CapabilityNotSupported(Exception):
    """Raised by a connector method the device/connector_type doesn't
    actually support. Routers translate this to a 4xx explaining why,
    never a 500 — the router still checks MfpDevice.cap_* before
    dispatching as the primary gate; this is the belt-and-suspenders
    fallback for when it doesn't (or can't, e.g. before capabilities have
    ever been assessed)."""


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str | None = None


@dataclass
class DeviceInfo:
    make_model: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None


@dataclass
class DeviceCapabilityReport:
    """Connector-reported capability booleans — a subset of
    MfpDevice's cap_* fields the connector was actually able to assess;
    fields it has no signal for are simply omitted (dict, not a fixed
    dataclass with every field defaulting False) so the router only
    overwrites what was actually checked."""

    capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass
class MeterSnapshot:
    total: int | None = None
    copy: int | None = None
    print: int | None = None
    confidence: str = "unsupported"  # verified | best_effort | unsupported
    vendor_profile_used: str | None = None


@dataclass
class AccountingPeriod:
    start: datetime
    end: datetime


@dataclass
class NormalizedUsageRow:
    """One row normalized into CopierUsageRecord's shape (app/models/
    copier_usage.py) — connectors return these; the caller (import/sync
    endpoint) persists them, so this stays a pure data shape with no DB
    session dependency."""

    external_identity_used: str
    activity_type: str = "copy"
    page_count: int | None = None
    sheet_count: int | None = None
    color_page_count: int | None = None
    monochrome_page_count: int | None = None
    duplex: bool | None = None
    paper_size: str | None = None
    occurred_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    authentication_method: str | None = None
    raw_payload: dict = field(default_factory=dict)


@dataclass
class SyncResult:
    synced_count: int = 0
    failed_count: int = 0
    message: str | None = None


@dataclass
class ImportResult:
    rows: list[NormalizedUsageRow] = field(default_factory=list)


class CopierConnector(ABC):
    """connector_type must match a key registered in
    app/copiers/registry.py:CONNECTOR_REGISTRY — that registry, not a
    Literal/enum, is the single source of truth for what's selectable in
    the UI (see MfpDevice.connector_type's docstring)."""

    connector_type: ClassVar[str]
    display_name: ClassVar[str]
    # Static, vendor-specific admin guidance (device menu paths, what to
    # export and from where, etc.) — surfaced in the frontend's connector
    # picker/device pages. None for the fully-generic connectors, which
    # have nothing vendor-specific to say.
    setup_notes: ClassVar[str | None] = None

    async def test_connection(self, device: MfpDevice) -> ConnectionTestResult:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't test a live connection."
        )

    async def get_device_info(self, device: MfpDevice) -> DeviceInfo:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't retrieve device info."
        )

    async def get_capabilities(self, device: MfpDevice) -> DeviceCapabilityReport:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't auto-detect capabilities."
        )

    async def get_meter_snapshot(self, device: MfpDevice) -> MeterSnapshot | None:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector doesn't support meter reads."
        )

    async def get_user_accounting(
        self, device: MfpDevice, period: AccountingPeriod
    ) -> list[NormalizedUsageRow]:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't retrieve per-user accounting directly "
            "— use a CSV import instead."
        )

    async def sync_users_to_device(
        self, device: MfpDevice, identities: list[StaffCopierIdentity]
    ) -> SyncResult:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't provision users to the device."
        )

    async def validate_user_mapping(self, device: MfpDevice, identity_value: str) -> str | None:
        """Returns the resolved staff_email for identity_value, or None if
        unresolved. Default implementation (used by generic_csv) looks up
        StaffCopierIdentity directly — most connectors won't need to
        override this at all."""
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector can't validate user mappings."
        )

    async def import_accounting_file(
        self, device: MfpDevice, raw_bytes: bytes, template: CopierImportTemplate
    ) -> ImportResult:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector doesn't support file import."
        )

    def normalize_usage_record(self, raw_row: dict, device: MfpDevice) -> NormalizedUsageRow:
        raise CapabilityNotSupported(
            f"The {self.connector_type} connector has no row-normalization logic."
        )


async def refresh_device_meter(device: MfpDevice, connector: CopierConnector) -> None:
    """Runs connector.get_meter_snapshot and applies the result to
    device's page_count_* fields in place — does not commit, matching
    app/printers/status.py's caller-owns-the-transaction convention.
    Connector-agnostic (any connector that implements get_meter_snapshot —
    generic_snmp, canon_department_id, ...), not tied to one
    implementation, so the router (app/routers/mfp_devices.py) can dispatch
    on the device's actual connector_type instead of hardcoding one.

    Only catches SnmpProbeError (a transient/operational failure — this
    one device didn't answer this time). CapabilityNotSupported is
    deliberately NOT caught here — it means the connector doesn't support
    meter reads at all, a structural fact the caller should surface as a
    4xx (see check_mfp_device_meter), not silently record as a per-device
    error like a network hiccup would be."""
    now = datetime.now(UTC)
    try:
        snapshot = await connector.get_meter_snapshot(device)
    except SnmpProbeError as exc:
        device.page_count_error = str(exc)
        device.page_count_checked_at = now
        return

    device.page_count_total = snapshot.total
    device.page_count_copy = snapshot.copy
    device.page_count_print = snapshot.print
    device.page_count_confidence = snapshot.confidence
    device.page_count_vendor_profile_used = snapshot.vendor_profile_used
    device.page_count_error = None
    device.page_count_checked_at = now
