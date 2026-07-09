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
    # Already available from CUPS argv before delivery even starts — see
    # infra/cups/backends/printops.
    document_name: str | None = None
    copy_count: int | None = None


class JobUpdate(BaseModel):
    status: str
    error_message: str | None = None
    # Known at creation time when CUPS hands the backend a filename; only
    # knowable here, after the fact, when CUPS instead piped the document
    # over stdin — see infra/cups/backends/printops.
    file_size_bytes: int | None = None
    # Physical sheets printed (CUPS job-media-sheets-completed), reported
    # best-effort by the CUPS backend script — see infra/cups/backends/printops.
    page_count: int | None = None
    # Best-effort, only knowable once the job completes — see
    # infra/cups/backends/printops:get_job_completion_attributes and
    # Job.color_mode's docstring (app/models/job.py) for why color_mode is a
    # per-job flag, not a per-page split.
    color_mode: str | None = None
    duplex: bool | None = None
    paper_size: str | None = None
    # Only sent alongside status="held" (app/routers/release.py) — where the
    # spooled file sits and the raw CUPS options string needed to replay
    # delivery later. held_expires_at is deliberately not accepted from the
    # caller; the server computes it from PrintReleaseSettings itself.
    held_file_path: str | None = None
    held_job_options: str | None = None


class JobOut(BaseModel):
    id: UUID
    printer_id: UUID
    cups_job_id: int | None
    submitted_by: str | None
    attribution_method: str
    file_size_bytes: int | None
    status: str
    # "pin_release" | "quota" | None — see Job.hold_reason's docstring
    # (app/models/job.py) for why status="held" alone isn't enough anymore.
    hold_reason: str | None
    error_message: str | None
    page_count: int | None
    document_name: str | None
    copy_count: int | None
    color_mode: str | None
    duplex: bool | None
    paper_size: str | None
    source: str
    completed_at: datetime | None
    held_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobListOut(JobOut):
    printer_name: str
    # Resolved from Job.mac_address via app/reports/aggregation.py:
    # resolve_device_names — the Mosyle/Google Workspace device name if
    # known, the raw MAC if not resolved to a name yet, or None if this
    # job never got a MAC at all (e.g. an unresolved/manual submission).
    device_name: str | None = None


class UserUsageOut(BaseModel):
    """One row of the usage report — either a synced Google Workspace
    roster user (email/name set, zero-filled if they've never printed) or
    the single aggregated `is_other` row covering everything printed under
    a name/email that isn't in the roster (e.g. attribution_method
    "unresolved", or a local username that never matched a Workspace
    address) — see app/routers/jobs.py:list_job_usage."""

    email: str | None
    name: str | None
    is_other: bool = False
    job_count: int
    total_pages: int
    total_bytes: int
