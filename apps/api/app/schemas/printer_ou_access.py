from uuid import UUID

from pydantic import BaseModel


class PrinterAllowedOuCreate(BaseModel):
    ou_path: str


class PrinterAllowedOuOut(BaseModel):
    id: UUID
    printer_id: UUID
    ou_path: str

    model_config = {"from_attributes": True}
