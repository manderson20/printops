import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CopierImportTemplate(Base, TimestampMixin):
    """A reusable column-mapping for a vendor's (and optionally a specific
    model's) accounting CSV export — saved once so a weekly/monthly
    re-import is upload-then-commit instead of re-mapping columns every
    time. See app/copiers/generic_csv.py for how column_mapping is
    applied."""

    __tablename__ = "copier_import_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    name: Mapped[str]
    vendor: Mapped[str]
    model: Mapped[str | None] = mapped_column(default=None)  # null = any model of this vendor

    # target field -> source column name, e.g. {"identity_value": "User
    # Code", "page_count": "Total Pages"}.
    column_mapping: Mapped[dict] = mapped_column(JSON)
    # Which StaffCopierIdentity.identity_type the mapped identity column
    # represents for every row imported with this template.
    identity_type: Mapped[str]
    delimiter: Mapped[str] = mapped_column(default=",", server_default=",")

    created_by: Mapped[str | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(default=None)


class CopierImportBatch(Base, TimestampMixin):
    """One uploaded accounting export and its processing lifecycle
    (uploaded -> previewed -> committed, or failed). Stage 1 scopes one
    batch to exactly one device — no per-row device column — matching the
    common case of a single copier's admin-panel export; a multi-device
    export is an additive future change (an optional device column on the
    template), not precluded by this shape.

    error_detail stores per-row parse errors inline rather than a
    dedicated errors table — expected volume here is weekly/monthly
    exports (hundreds, not millions, of rows), so this is a deliberate
    simplicity choice, not an oversight."""

    __tablename__ = "copier_import_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    mfp_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("copier_import_templates.id", ondelete="SET NULL"), default=None
    )

    original_filename: Mapped[str]
    raw_file_path: Mapped[str]
    uploaded_by: Mapped[str]
    period_label: Mapped[str | None] = mapped_column(default=None)

    # uploaded -> previewed -> committed | failed
    status: Mapped[str] = mapped_column(default="uploaded", server_default="uploaded")
    # Snapshot of the mapping actually used, so commit doesn't require the
    # frontend to resend it after preview.
    column_mapping: Mapped[dict | None] = mapped_column(JSON, default=None)
    identity_type: Mapped[str | None] = mapped_column(default=None)

    row_count: Mapped[int] = mapped_column(default=0, server_default="0")
    imported_row_count: Mapped[int] = mapped_column(default=0, server_default="0")
    duplicate_row_count: Mapped[int] = mapped_column(default=0, server_default="0")
    unmapped_identity_count: Mapped[int] = mapped_column(default=0, server_default="0")
    error_detail: Mapped[list | None] = mapped_column(JSON, default=None)  # [{row_number, message}]

    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
