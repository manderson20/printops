import uuid

from sqlalchemy import ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PrinterReleaseBypass(Base, TimestampMixin):
    """A user who skips the PIN-release hold at one specific printer, even
    while that printer's release_required is on — e.g. a secretary who
    sits next to the copier shouldn't need to walk through the kiosk PIN
    for jobs everyone else at that printer must release manually. Unique
    per (printer_id, user_email), same FK/index shape as PrinterUserQuota
    (app/models/quota.py) — no default/wildcard row concept here, though,
    since "everyone bypasses" is just release_required=False."""

    __tablename__ = "printer_release_bypasses"
    __table_args__ = (
        UniqueConstraint("printer_id", "user_email", name="uq_printer_release_bypass_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True
    )
    user_email: Mapped[str]
