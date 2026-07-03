import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SnmpDefaultsSettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as MosyleSettings) — org-wide SNMP
    defaults for the counter poll loop (app/printers/snmp_counters.py).
    Individual printers can override any of these (Printer.snmp_port/
    snmp_version/snmp_community_encrypted/snmp_vendor_profile) for the odd
    device configured differently.

    `enabled` defaults false (matches every other settings model that talks
    to external devices with credential-like config — Mosyle, ClassGuard,
    Google Workspace) so nothing polls until an admin opts in, even though
    `community_encrypted` gets seeded with "public" on first creation (the
    confirmed working default across this district's real fleet)."""

    __tablename__ = "snmp_defaults_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    community_encrypted: Mapped[str | None] = mapped_column(default=None)
    version: Mapped[str] = mapped_column(default="v2c", server_default="v2c")
    port: Mapped[int] = mapped_column(default=161, server_default="161")
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Governs the purge loop (app/main.py:_counter_reading_purge_loop) that
    # deletes old PrinterCounterReading rows below.
    retention_days: Mapped[int] = mapped_column(default=180, server_default="180")


class PrinterCounterReading(Base):
    """An append-only history of SNMP counter polls (app/printers/
    snmp_counters.py) — unlike Printer.page_count_*, which is overwritten
    each poll (current value only), this accumulates one row per
    successful read so app/printers/counter_history.py can compute
    period deltas ("pages produced this week") for a per-printer usage
    chart. The first append-only/history table in this codebase — modeled
    on PrinterTonerCartridge's FK/index shape (app/models/report.py), not
    TimestampMixin's created_at/updated_at pair, since recorded_at (when
    the SNMP read actually happened) is the only timestamp that matters
    here. Field names mirror Printer.page_count_* exactly — same concept,
    a historical snapshot of those same fields."""

    __tablename__ = "printer_counter_readings"
    __table_args__ = (
        Index("ix_printer_counter_readings_printer_recorded", "printer_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    page_count_total: Mapped[int | None] = mapped_column(default=None)
    page_count_copy: Mapped[int | None] = mapped_column(default=None)
    page_count_print: Mapped[int | None] = mapped_column(default=None)
    page_count_confidence: Mapped[str | None] = mapped_column(default=None)
