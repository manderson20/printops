from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, IPvAnyAddress

from app.schemas.snmp import SnmpVersion, VendorProfile


class CapabilitiesOut(BaseModel):
    make_model: str | None = None
    firmware_version: str | None = None
    duplex_supported: bool = False
    color_supported: bool = False
    copies_max: int | None = None
    resolutions: list[dict] = []
    media_sizes: list[str] = []
    media_sources: list[str] = []
    media_types: list[str] = []
    output_bins: list[str] = []
    finishings: list[str] = []
    collation_supported: bool = False
    pin_printing_supported: bool = False
    accounting_supported: bool = False
    document_formats: list[str] = []


class PrinterCreate(BaseModel):
    name: str
    ip_address: IPvAnyAddress
    port: int = 631
    use_tls: bool = False
    ipp_path: str | None = None
    # Off by default — a new printer isn't visible to AirPrint discovery on
    # the subnet until an admin explicitly opts in (see Printer model).
    airprint_enabled: bool = False

    manufacturer: str | None = None
    model: str | None = None
    hostname: str | None = None
    serial_number: str | None = None
    building: str | None = None
    room: str | None = None
    department: str | None = None
    notes: str | None = None

    snmp_enabled: bool = True
    snmp_port: int | None = None
    snmp_version: SnmpVersion | None = None
    snmp_community: str | None = None
    snmp_vendor_profile: VendorProfile | None = None


class PrinterUpdate(BaseModel):
    name: str | None = None
    ip_address: IPvAnyAddress | None = None
    port: int | None = None
    use_tls: bool | None = None
    ipp_path: str | None = None
    airprint_enabled: bool | None = None

    manufacturer: str | None = None
    model: str | None = None
    hostname: str | None = None
    serial_number: str | None = None
    building: str | None = None
    room: str | None = None
    department: str | None = None
    notes: str | None = None

    release_required: bool | None = None

    snmp_enabled: bool | None = None
    snmp_port: int | None = None
    # "" is accepted alongside the real values as an explicit "clear this
    # override, fall back to the global default" signal (see update_printer
    # in routers/printers.py) — a plain SnmpVersion|None would 422 on "",
    # since Pydantic's Literal validation rejects it before that logic runs.
    snmp_version: Literal["v1", "v2c", ""] | None = None
    snmp_community: str | None = None
    snmp_vendor_profile: Literal[
        "canon", "konica_minolta", "hp", "lexmark", "kyocera", "generic", ""
    ] | None = None

    ldap_enabled: bool | None = None
    ldap_bind_username: str | None = None
    # Write-only, like snmp_community above — never echoed back (see
    # PrinterOut's has_ldap_bind_password), hashed (not encrypted) into
    # ldap_bind_password_hash on write (see routers/printers.py).
    ldap_bind_password: str | None = None


class PrinterConnectionOut(BaseModel):
    """Minimal connection + capability summary for the CUPS backend script and
    the Avahi service-file generator — not the full printer record, and
    authenticated with the backend token, not user JWT."""

    name: str
    ip_address: str
    port: int
    use_tls: bool
    ipp_path: str | None
    airprint_enabled: bool
    capabilities: CapabilitiesOut | None
    release_required: bool

    model_config = {"from_attributes": True}


class PrinterMdmConnectionOut(BaseModel):
    """What an admin needs to manually add this printer's PrintOps-proxied
    queue in an MDM tool (e.g. Mosyle) — the CUPS server's own LAN address,
    not the real printer's — since clients print to PrintOps, not directly
    to the device. See Settings.print_server_host."""

    queue_name: str
    host: str
    port: int
    resource_path: str
    ipp_uri: str
    airprint_enabled: bool


class PrinterOut(BaseModel):
    id: UUID
    name: str
    ip_address: str
    port: int
    use_tls: bool
    ipp_path: str | None
    airprint_enabled: bool

    manufacturer: str | None
    model: str | None
    hostname: str | None
    serial_number: str | None
    building: str | None
    room: str | None
    department: str | None
    notes: str | None

    capabilities: CapabilitiesOut | None
    capabilities_detected_at: datetime | None
    capabilities_error: str | None
    queue_sync_error: str | None

    status: str
    status_reasons: list[str] | None
    status_message: str | None
    status_checked_at: datetime | None

    release_required: bool
    release_token: str | None

    snmp_enabled: bool
    snmp_port: int | None
    snmp_version: SnmpVersion | None
    has_snmp_community: bool
    snmp_vendor_profile: VendorProfile | None

    ldap_enabled: bool
    ldap_bind_username: str | None
    has_ldap_bind_password: bool

    page_count_total: int | None
    page_count_copy: int | None
    page_count_print: int | None
    page_count_confidence: str | None
    page_count_vendor_profile_used: str | None
    page_count_checked_at: datetime | None
    page_count_error: str | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
