from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.copiers.registry import CONNECTOR_REGISTRY
from app.schemas.snmp import SnmpVersion

# The full target vendor list from the copier-accounting spec — broader
# than app/schemas/snmp.py's VendorProfile, which is scoped to vendors
# with a tested SNMP meter breakdown. "Which manufacturer" (this field) is
# independent of "which connector talks to it" (connector_type below) —
# a Canon device can exist today with connector_type="generic_csv" long
# before a real Canon connector ships.
MfpVendor = Literal[
    "canon",
    "konica_minolta",
    "hp",
    "lexmark",
    "kyocera",
    "ricoh",
    "sharp",
    "xerox",
    "generic",
]

# Restricted to whatever's actually registered (app/copiers/registry.py) —
# Stage 1 ships "generic_csv"/"generic_snmp" only. Not a static Literal so
# later stages can register a connector without a schema change; enforced
# at runtime in the router instead (see create_mfp_device/update_mfp_device).
ConnectorType = str


class DeviceCapabilities(BaseModel):
    """None = not yet assessed, distinct from False = confirmed
    unsupported — see MfpDevice's docstring."""

    walkup_copy_accounting: bool | None = None
    user_code_pin_auth: bool | None = None
    badge_card_auth: bool | None = None
    department_id_accounting: bool | None = None
    ldap_auth: bool | None = None
    local_user_table: bool | None = None
    remote_user_provisioning: bool | None = None
    csv_accounting_export: bool | None = None
    api_accounting_retrieval: bool | None = None
    snmp_meter_counters: bool | None = None
    scan_accounting: bool | None = None
    color_mono_accounting: bool | None = None
    quotas: bool | None = None
    secure_print_release: bool | None = None


class MfpDeviceCreate(BaseModel):
    name: str
    vendor: MfpVendor = "generic"
    model: str | None = None
    serial_number: str | None = None

    printer_id: UUID | None = None
    ip_address: str | None = None
    hostname: str | None = None

    building: str | None = None
    room: str | None = None
    department: str | None = None

    connector_type: ConnectorType = "generic_csv"
    connector_config: dict | None = None

    snmp_enabled: bool = False
    snmp_port: int | None = None
    snmp_version: SnmpVersion | None = None
    snmp_community: str | None = None
    snmp_vendor_profile: MfpVendor | None = None

    notes: str | None = None


class MfpDeviceUpdate(BaseModel):
    name: str | None = None
    vendor: MfpVendor | None = None
    model: str | None = None
    serial_number: str | None = None

    printer_id: UUID | None = None
    ip_address: str | None = None
    hostname: str | None = None

    building: str | None = None
    room: str | None = None
    department: str | None = None

    connector_type: ConnectorType | None = None
    connector_config: dict | None = None

    snmp_enabled: bool | None = None
    snmp_port: int | None = None
    snmp_version: Literal["v1", "v2c", ""] | None = None
    snmp_community: str | None = None
    snmp_vendor_profile: str | None = None

    # Manual capability overrides — connector-run checks
    # (check-capabilities) also write these fields, but an admin can set
    # them by hand for a device with no automatable capability probe at
    # all (matches the spec's "capability profile is often manually
    # curated" expectation for placeholder/manual-only connectors).
    capabilities: DeviceCapabilities | None = None

    notes: str | None = None


class MfpDeviceOut(BaseModel):
    id: UUID
    printer_id: UUID | None
    name: str
    vendor: str
    model: str | None
    serial_number: str | None
    ip_address: str | None
    hostname: str | None
    building: str | None
    room: str | None
    department: str | None

    connector_type: str
    connector_config: dict | None

    capabilities: DeviceCapabilities
    capabilities_source: str | None
    capabilities_detected_at: datetime | None

    snmp_enabled: bool
    snmp_port: int | None
    snmp_version: SnmpVersion | None
    has_snmp_community: bool
    snmp_vendor_profile: str | None

    page_count_total: int | None
    page_count_copy: int | None
    page_count_print: int | None
    page_count_confidence: str | None
    page_count_vendor_profile_used: str | None
    page_count_checked_at: datetime | None
    page_count_error: str | None

    last_test_connection_at: datetime | None
    last_test_connection_ok: bool | None
    last_test_connection_message: str | None

    notes: str | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectorTypeOut(BaseModel):
    """What the frontend's connector-type picker actually offers — only
    what's registered, never a fake/unimplemented option (see
    app/copiers/registry.py)."""

    connector_type: str
    label: str
    setup_notes: str | None = None


def available_connector_types() -> list[ConnectorTypeOut]:
    return [
        ConnectorTypeOut(connector_type=key, label=connector.display_name, setup_notes=connector.setup_notes)
        for key, connector in CONNECTOR_REGISTRY.items()
    ]
