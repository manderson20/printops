from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, IPvAnyAddress


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


class PrinterCreate(BaseModel):
    name: str
    ip_address: IPvAnyAddress
    port: int = 631
    use_tls: bool = False
    ipp_path: str | None = None

    manufacturer: str | None = None
    model: str | None = None
    hostname: str | None = None
    serial_number: str | None = None
    building: str | None = None
    room: str | None = None
    department: str | None = None
    notes: str | None = None


class PrinterUpdate(BaseModel):
    name: str | None = None
    ip_address: IPvAnyAddress | None = None
    port: int | None = None
    use_tls: bool | None = None
    ipp_path: str | None = None

    manufacturer: str | None = None
    model: str | None = None
    hostname: str | None = None
    serial_number: str | None = None
    building: str | None = None
    room: str | None = None
    department: str | None = None
    notes: str | None = None


class PrinterConnectionOut(BaseModel):
    """Minimal connection info for the CUPS backend script — not the full
    printer record, and authenticated with the backend token, not user JWT."""

    name: str
    ip_address: str
    port: int
    use_tls: bool
    ipp_path: str | None

    model_config = {"from_attributes": True}


class PrinterOut(BaseModel):
    id: UUID
    name: str
    ip_address: str
    port: int
    use_tls: bool
    ipp_path: str | None

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

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
