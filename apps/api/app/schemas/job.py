from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class JobCreate(BaseModel):
    printer_id: UUID
    cups_job_id: int | None = None
    submitted_by: str | None = None
    file_size_bytes: int | None = None
    # Client IP (job-originating-host-name), used for MDM-based attribution
    # (app/attribution/resolve.py) — see infra/cups/backends/printops.
    source_host: str | None = None


class JobUpdate(BaseModel):
    status: str
    error_message: str | None = None
    # Physical sheets printed (CUPS job-media-sheets-completed), reported
    # best-effort by the CUPS backend script — see infra/cups/backends/printops.
    page_count: int | None = None


class JobOut(BaseModel):
    id: UUID
    printer_id: UUID
    cups_job_id: int | None
    submitted_by: str | None
    attribution_method: str
    file_size_bytes: int | None
    status: str
    error_message: str | None
    page_count: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListOut(JobOut):
    printer_name: str


class UserUsageOut(BaseModel):
    submitted_by: str | None
    job_count: int
    total_pages: int
    total_bytes: int
