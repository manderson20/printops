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
    # What this printer will actually honour at submission time, from its own
    # discovered capabilities. Empty means "never probed, or has no finisher" —
    # the page shows no options rather than offering ones that would be
    # silently dropped.
    supports_duplex: bool = False
    finishings: list[str] = []

    model_config = {"from_attributes": True}


class SelfServicePrintResultOut(BaseModel):
    printer_id: UUID
    printer_name: str
    filename: str
    copies: int
