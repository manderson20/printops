from uuid import UUID

from pydantic import BaseModel


class SelfServicePrinterOut(BaseModel):
    """Deliberately minimal — this list is shown to every authenticated
    user (students included, once self-service printing rolls out), not
    just admins, so it excludes everything PrinterOut exposes beyond what
    someone picking a printer to print to actually needs."""

    id: UUID
    name: str
    building: str | None
    room: str | None
    department: str | None
    is_virtual: bool

    model_config = {"from_attributes": True}


class SelfServicePrintResultOut(BaseModel):
    printer_id: UUID
    printer_name: str
    filename: str
    copies: int
