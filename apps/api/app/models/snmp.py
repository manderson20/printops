import uuid

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
