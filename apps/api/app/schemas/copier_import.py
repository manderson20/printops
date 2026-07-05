from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class CopierImportTemplateCreate(BaseModel):
    name: str
    vendor: str
    model: str | None = None
    column_mapping: dict[str, str]
    identity_type: str
    delimiter: str = ","
    notes: str | None = None


class CopierImportTemplateUpdate(BaseModel):
    name: str | None = None
    vendor: str | None = None
    model: str | None = None
    column_mapping: dict[str, str] | None = None
    identity_type: str | None = None
    delimiter: str | None = None
    notes: str | None = None


class CopierImportTemplateOut(BaseModel):
    id: UUID
    name: str
    vendor: str
    model: str | None
    column_mapping: dict[str, str]
    identity_type: str
    delimiter: str
    created_by: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CopierImportUploadOut(BaseModel):
    """Response to POST /upload — everything the frontend's mapping step
    needs, whether or not a template was supplied."""

    batch_id: UUID
    original_filename: str
    header: list[str]
    sample_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str] | None
    suggested_identity_type: str | None
    row_count: int


class CopierImportPreviewRequest(BaseModel):
    column_mapping: dict[str, str]
    identity_type: str
    period_label: str | None = None
    save_as_template: CopierImportTemplateCreate | None = None


class PreviewRowOut(BaseModel):
    row_number: int
    external_identity_used: str | None
    staff_email: str | None
    is_duplicate: bool
    error: str | None


class CopierImportPreviewOut(BaseModel):
    batch_id: UUID
    total_rows: int
    valid_rows: int
    duplicate_rows: int
    unmapped_rows: int
    error_rows: int
    sample_rows: list[PreviewRowOut]
    saved_template_id: UUID | None = None


class CopierImportCommitRequest(BaseModel):
    skip_duplicates: bool = True


class CopierImportBatchOut(BaseModel):
    id: UUID
    mfp_device_id: UUID
    template_id: UUID | None
    original_filename: str
    uploaded_by: str
    period_label: str | None
    status: str
    column_mapping: dict[str, str] | None
    identity_type: str | None
    row_count: int
    imported_row_count: int
    duplicate_row_count: int
    unmapped_identity_count: int
    error_detail: list[dict] | None
    committed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
