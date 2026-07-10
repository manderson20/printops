import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CopierUsageRecord(Base, TimestampMixin):
    """A single normalized walk-up copier usage event or accounting-period
    row, from any connector (app/copiers/). This is the append-only ledger
    that combined print+copy reporting (app/reports/aggregation.py) reads
    alongside Job — staff_email is deliberately the same loose string join
    key Job.submitted_by already uses, not an FK, so the two can be merged
    by simple equality without a schema change on either side.

    Unlike PrinterCounterReading (pure append, never edited),
    updated_at is meaningful here: the Unmapped Activity "reprocess" flow
    (app/routers/copier_unmapped.py) rewrites staff_email/
    staff_employee_id/external_identity_type on existing rows in place
    once an admin maps a previously-unknown identity."""

    __tablename__ = "copier_usage_records"
    __table_args__ = (
        Index("ix_copier_usage_records_device_occurred", "mfp_device_id", "occurred_at"),
        Index(
            "ix_copier_usage_records_identity",
            "external_identity_type",
            "external_identity_used",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    mfp_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from MfpDevice at import/record time — same rationale
    # as app/reports/aggregation.py's CostRawRow: historical accuracy even
    # if the device record is later edited/reclassified.
    vendor: Mapped[str]
    model: Mapped[str | None] = mapped_column(default=None)
    serial_number: Mapped[str | None] = mapped_column(default=None)
    location_building: Mapped[str | None] = mapped_column(default=None)

    # Null = unresolved/unmapped — the join key for combined reporting,
    # same shape as Job.submitted_by.
    staff_email: Mapped[str | None] = mapped_column(index=True, default=None)
    staff_employee_id: Mapped[str | None] = mapped_column(default=None)

    # Raw code as it appeared in the source row, kept even after
    # resolution for audit/troubleshooting.
    external_identity_used: Mapped[str] = mapped_column(index=True)
    # The matched StaffCopierIdentity.identity_type, or null if unmapped.
    external_identity_type: Mapped[str | None] = mapped_column(default=None, index=True)
    # Device/vendor-reported auth method — distinct from
    # external_identity_type (that's our normalized identity category;
    # this is what the source actually says happened at the glass, e.g.
    # "badge_nfc"). For Stage 1's CSV/SNMP connectors these are often
    # equal or one is null; the distinction matters once a later
    # connector's payload actually separates them.
    authentication_method: Mapped[str | None] = mapped_column(default=None)

    activity_type: Mapped[str] = mapped_column(
        default="copy", server_default="copy"
    )  # copy|scan|fax|unknown

    page_count: Mapped[int | None] = mapped_column(default=None)
    sheet_count: Mapped[int | None] = mapped_column(default=None)
    color_page_count: Mapped[int | None] = mapped_column(default=None)
    monochrome_page_count: Mapped[int | None] = mapped_column(default=None)
    duplex: Mapped[bool | None] = mapped_column(default=None)
    paper_size: Mapped[str | None] = mapped_column(default=None)

    # Single-event timestamp when the source data has one.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Or an accounting-period range, for aggregate/summary export rows
    # (common for department-ID accounting exports). At least one of
    # occurred_at or this pair is set — validated at parse time
    # (app/copiers/generic_csv.py), not a DB constraint.
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    source_connector: Mapped[str]  # generic_csv | generic_snmp in Stage 1
    # Null for the SNMP connector, which never writes to this table at all
    # (no per-user identity to attach — it only updates
    # MfpDevice.page_count_*).
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("copier_import_batches.id", ondelete="SET NULL"), index=True, default=None
    )

    raw_payload: Mapped[dict] = mapped_column(JSON)
