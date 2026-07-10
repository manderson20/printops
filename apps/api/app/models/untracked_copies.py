import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UntrackedCopySettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as QuotaSettings) — the org-wide
    kill switch for the Untracked Copy Activity report
    (app/reports/untracked_copies.py). Off by default. Unlike every other
    settings singleton in this codebase, this one also stamps
    `enabled_at` — SNMP counter history (PrinterCounterReading) already
    exists from before this feature ships, and the report must never
    treat pre-enable deltas as measured/estimated copies, only ever
    compute going forward from when an admin actually turned it on. A
    disable-then-re-enable is treated as a fresh start (enabled_at is
    re-stamped), not a resume of the old window — see
    app/routers/settings.py's PUT handler for where this is set."""

    __tablename__ = "untracked_copy_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
