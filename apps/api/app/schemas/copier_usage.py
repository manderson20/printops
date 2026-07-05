from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CopierUsageRecordOut(BaseModel):
    id: UUID
    mfp_device_id: UUID
    vendor: str
    model: str | None
    serial_number: str | None
    location_building: str | None

    staff_email: str | None
    staff_employee_id: str | None
    external_identity_used: str
    external_identity_type: str | None
    authentication_method: str | None

    activity_type: str
    page_count: int | None
    sheet_count: int | None
    color_page_count: int | None
    monochrome_page_count: int | None
    duplex: bool | None
    paper_size: str | None

    occurred_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None

    source_connector: str
    import_batch_id: UUID | None

    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
