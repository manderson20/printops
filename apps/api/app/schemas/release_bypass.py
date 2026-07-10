from uuid import UUID

from pydantic import BaseModel


class PrinterReleaseBypassCreate(BaseModel):
    user_email: str


class PrinterReleaseBypassOut(BaseModel):
    id: UUID
    printer_id: UUID
    user_email: str

    model_config = {"from_attributes": True}
