import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ClassGuardSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, created/updated via the
    Integrations UI. ClassGuard is a self-hosted DHCP/DNS/web-filter
    platform each deployment runs its own instance of — used here purely
    as a live IP->MAC lookup (its DHCP lease table) to resolve a print
    job's source IP to a MAC address for Mosyle device matching
    (app/attribution/resolve.py). Unlike Mosyle's device roster, lease
    data changes fast and is looked up live per job, not cached — no
    local cache table."""

    __tablename__ = "classguard_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # No org-specific default — every deployment points this at its own
    # ClassGuard instance via the Integrations UI.
    base_url: Mapped[str] = mapped_column(default="")
    access_token_encrypted: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_test_error: Mapped[str | None] = mapped_column(default=None)
