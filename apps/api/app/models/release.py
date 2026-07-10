import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PrintReleaseSettings(Base, TimestampMixin):
    """Effectively a singleton (one row, same pattern as
    ReportFormulaSettings/MosyleSettings) — the admin-configurable retention
    window for held jobs (app/routers/release.py, Job.held_expires_at)."""

    __tablename__ = "print_release_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    hold_expiry_hours: Mapped[float] = mapped_column(default=48.0, server_default="48.0")
