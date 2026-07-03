import uuid

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Not yet exposed via the API — reserved so multi-tenancy doesn't require a
    # retrofit later (see ARCHITECTURE.md §6).
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    printer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True
    )
    cups_job_id: Mapped[int | None] = mapped_column(default=None)

    # Resolved via app/attribution/resolve.py's ordered strategy chain
    # (ARCHITECTURE.md §4) — not necessarily the raw CUPS-reported value
    # anymore. See attribution_method for how this was determined.
    submitted_by: Mapped[str | None] = mapped_column(default=None)

    # "cups" (trusted CUPS requesting-user-name), "mosyle"/"google_workspace"
    # (resolved via MDM device->user lookup), "override" (admin-set device
    # correction, app/models/device_override.py), or "unresolved" (fell
    # through every strategy — submitted_by is either the raw CUPS value or
    # "unknown").
    attribution_method: Mapped[str] = mapped_column(default="unresolved", server_default="unresolved")

    # The MAC address resolved for this job's source IP (via ClassGuard),
    # if any — independent of whether that MAC actually resolved to a user.
    # Captured so a later DeviceUserOverride can backfill this specific
    # job's attribution without touching any other job (see
    # app/routers/device_overrides.py). Only populated going forward from
    # when this column was added — not backfillable for older jobs, since
    # the MAC was never persisted before now.
    mac_address: Mapped[str | None] = mapped_column(index=True, default=None)

    file_size_bytes: Mapped[int | None] = mapped_column(default=None)

    # received -> forwarding -> forwarded | failed
    status: Mapped[str] = mapped_column(default="received", server_default="received")
    error_message: Mapped[str | None] = mapped_column(default=None)

    # Physical sheets printed (CUPS job-media-sheets-completed, read from the
    # local CUPS job record after forwarding completes — accounts for
    # duplex/copies). Reported best-effort by the CUPS backend script
    # (infra/cups/backends/printops); null if unavailable.
    page_count: Mapped[int | None] = mapped_column(default=None)
