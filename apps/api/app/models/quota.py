import uuid

from sqlalchemy import ForeignKey, Index, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

QUOTA_PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")


class QuotaSettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as SnmpDefaultsSettings) — the
    org-wide kill switch for page-quota enforcement. `enabled` defaults
    false so configuring PrinterUserQuota rows never starts holding jobs
    until an admin explicitly opts in (see app/quotas/service.py:
    resolve_hold_reason, app/routers/jobs.py:create_job)."""

    __tablename__ = "quota_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")


class PrinterUserQuota(Base, TimestampMixin):
    """A page-count limit for one user at one printer, over a period the
    admin configuring it chooses — unique per (printer_id, user_email),
    same FK/index shape as PrinterTonerCartridge (app/models/report.py).
    `user_email = None` is a per-printer default/wildcard row ("anyone
    without their own row at this printer"), enforced to at most one per
    printer via a partial unique index (see the migration) rather than a
    separate table — mirrors how Printer's nullable SNMP override columns
    mean "fall back to the global default" without a second model."""

    __tablename__ = "printer_user_quotas"
    __table_args__ = (
        UniqueConstraint("printer_id", "user_email", name="uq_printer_user_quota_email"),
        # At most one default/wildcard row (user_email IS NULL) per printer —
        # the UniqueConstraint above doesn't cover this since SQL NULL never
        # equals NULL. Defined here (not just in the migration) since tests
        # build tables straight from this metadata via Base.metadata.create_all,
        # bypassing Alembic entirely.
        Index(
            "uq_printer_user_quotas_default",
            "printer_id",
            unique=True,
            sqlite_where=text("user_email IS NULL"),
            postgresql_where=text("user_email IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True
    )
    # None = default/wildcard row for this printer (see class docstring).
    user_email: Mapped[str | None] = mapped_column(default=None)
    # "daily" | "weekly" | "monthly" | "quarterly" | "yearly" — enforced at
    # the API layer (schemas/quota.py), not a DB constraint, same convention
    # as PrinterTonerCartridge.color.
    period: Mapped[str]
    page_limit: Mapped[int]
