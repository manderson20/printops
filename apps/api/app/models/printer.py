import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Printer(Base, TimestampMixin):
    __tablename__ = "printers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Not yet exposed via the API — reserved so multi-tenancy doesn't require a
    # retrofit later (see ARCHITECTURE.md §6).
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    name: Mapped[str]
    manufacturer: Mapped[str | None] = mapped_column(default=None)
    model: Mapped[str | None] = mapped_column(default=None)

    ip_address: Mapped[str]
    hostname: Mapped[str | None] = mapped_column(default=None)
    serial_number: Mapped[str | None] = mapped_column(default=None)

    port: Mapped[int] = mapped_column(default=631, server_default="631")
    use_tls: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Optional override for the IPP resource path (e.g. "/printers/queue-name")
    # when the default candidate-path probing doesn't find the printer.
    ipp_path: Mapped[str | None] = mapped_column(default=None)

    building: Mapped[str | None] = mapped_column(default=None)
    room: Mapped[str | None] = mapped_column(default=None)
    department: Mapped[str | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(default=None)

    capabilities: Mapped[dict | None] = mapped_column(JSON, default=None)
    capabilities_raw: Mapped[dict | None] = mapped_column(JSON, default=None)
    capabilities_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    capabilities_error: Mapped[str | None] = mapped_column(default=None)
