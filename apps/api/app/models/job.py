import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid
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

    # received -> forwarding -> forwarded | failed | cancelled
    #          -> held -> forwarding -> forwarded | failed | cancelled
    # "held" is set by the CUPS backend script instead of forwarding, for a
    # printer with Printer.release_required OR a user over their page quota
    # (app/quotas/service.py) — see hold_reason below for which, and
    # app/routers/release.py (the public kiosk API that resolves a PIN and
    # releases it) and app/printers/release.py (the actual delivery once
    # released). A held job also reaches "cancelled" if it's never released
    # before held_expires_at (app/main.py's background purge loop).
    # "cancelled" from "forwarding" is set by an admin action
    # (app/routers/jobs.py:cancel_job or app/routers/printers.py:purge_jobs)
    # — only reachable from "forwarding"/"held", since forwarded/failed jobs
    # are already terminal.
    status: Mapped[str] = mapped_column(default="received", server_default="received")
    # Why status="held" — "pin_release" (Printer.release_required, releasable
    # by the submitter's own PIN at the kiosk) or "quota" (over a
    # PrinterUserQuota limit, releasable only by an admin — see
    # app/routers/quota_holds.py). None until the job is actually held.
    # Decided once, at create_job time (app/quotas/service.py:resolve_hold_reason),
    # even though the CUPS backend script doesn't act on it (spool + PATCH
    # status="held") until slightly later in the same request — see
    # infra/cups/backends/printops. The self-service PIN kiosk
    # (app/routers/release.py) filters to hold_reason="pin_release" only, so
    # a quota hold can never be released there.
    hold_reason: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)

    # Physical sheets printed (CUPS job-media-sheets-completed, read from the
    # local CUPS job record after forwarding completes — accounts for
    # duplex/copies). Reported best-effort by the CUPS backend script
    # (infra/cups/backends/printops); null if unavailable.
    page_count: Mapped[int | None] = mapped_column(default=None)

    # --- Print Insights fields (app/reports/) ---
    # job-title/job-copies are already handed to the CUPS backend script as
    # argv before delivery even starts — captured at create_job time.
    document_name: Mapped[str | None] = mapped_column(default=None)
    copy_count: Mapped[int | None] = mapped_column(default=None)

    # The rest are only knowable from the completed job's IPP attributes
    # (like page_count above) — captured at update_job time, best-effort,
    # None if the printer/CUPS didn't report it. Note: this is a per-job
    # color mode, not a per-page split — consumer/office copier IPP
    # interfaces don't expose a color/mono breakdown within one job (see
    # app/reports/aggregation.py, which derives "color pages"/"mono pages"
    # rollups from this flag + page_count rather than storing them
    # separately).
    color_mode: Mapped[str | None] = mapped_column(default=None)
    duplex: Mapped[bool | None] = mapped_column(default=None)
    paper_size: Mapped[str | None] = mapped_column(default=None)

    # Where this job record came from — always "cups" today; reserved for a
    # future manual/copier-log import path (see app/reports/ CSV export for
    # the mirror-image "export", not an import).
    source: Mapped[str] = mapped_column(default="cups", server_default="cups")

    # Set when status becomes terminal (forwarded/failed/cancelled) —
    # created_at already serves as "submitted_at" for reporting purposes.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # --- Print-and-release fields (app/routers/release.py) ---
    # Where the spooled document sits while status="held"
    # (/var/spool/printops-held/<job-id>) — cleared once released
    # (app/printers/release.py) or purged after held_expires_at.
    held_file_path: Mapped[str | None] = mapped_column(default=None)
    # The raw CUPS options string handed to the backend script at submission
    # time — replayed at release time to reconstruct the same real_argv
    # shape the backend would have used for an immediate forward.
    held_job_options: Mapped[str | None] = mapped_column(default=None)
    # Computed server-side from PrintReleaseSettings.hold_expiry_hours when
    # the job is held — never trusted from the CUPS backend script's own
    # clock.
    held_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
