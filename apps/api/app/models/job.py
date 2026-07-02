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

    # Whatever CUPS reports as the submitting user — not yet verified against
    # the attribution fallback chain in ARCHITECTURE.md §4 (MDM/Google Admin
    # lookups). Treat as provisional until that module exists.
    submitted_by: Mapped[str | None] = mapped_column(default=None)

    file_size_bytes: Mapped[int | None] = mapped_column(default=None)

    # received -> forwarding -> forwarded | failed
    status: Mapped[str] = mapped_column(default="received", server_default="received")
    error_message: Mapped[str | None] = mapped_column(default=None)
