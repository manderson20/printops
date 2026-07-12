import uuid

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PrinterAllowedOu(Base, TimestampMixin):
    """Restricts which Google Workspace org units may target this printer
    via self-service web upload printing (app/self_service_print/) —
    unrelated to normal AirPrint/MDM printing, which has no per-user
    access control at all (see app/routers/jobs.py:create_job). Same
    FK/index/unique shape as PrinterReleaseBypass (app/models/
    release_bypass.py).

    No rows for a printer = open to every authenticated user — same
    "permissive by default, opt-in to restrict" convention as
    release_required/follow_me_enabled/quotas. Once an admin adds at
    least one row, the printer becomes restricted to just those OUs
    (and anything nested under them, via
    app.integrations.google_workspace.org_unit_matches — the same
    prefix-matching already used for OU-Viewer report scoping)."""

    __tablename__ = "printer_allowed_ous"
    __table_args__ = (
        UniqueConstraint("printer_id", "ou_path", name="uq_printer_allowed_ou_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True
    )
    ou_path: Mapped[str]
