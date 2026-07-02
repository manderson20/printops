import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MosyleSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, created/updated via the Settings
    UI. Credentials are stored encrypted (app/core/crypto.py), never
    returned decrypted by the API — see PrinterMdmConnectionOut-style
    masking in the settings router."""

    __tablename__ = "mosyle_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    base_url: Mapped[str] = mapped_column(default="https://businessapi.mosyle.com/v1")
    access_token_encrypted: Mapped[str | None] = mapped_column(default=None)
    admin_email: Mapped[str | None] = mapped_column(default=None)
    admin_password_encrypted: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_error: Mapped[str | None] = mapped_column(default=None)
    device_count: Mapped[int] = mapped_column(default=0, server_default="0")


class MosyleDevice(Base):
    """Local cache of Mosyle's device inventory, refreshed periodically by
    app/integrations/mosyle.py's sync_devices — never queried live per
    print job (see app/attribution/resolve.py)."""

    __tablename__ = "mosyle_devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Uppercased, colon-separated (AA:BB:CC:DD:EE:FF) for consistent lookup.
    mac_address: Mapped[str] = mapped_column(index=True, unique=True)
    serial_number: Mapped[str | None] = mapped_column(default=None)
    device_name: Mapped[str | None] = mapped_column(default=None)
    user_email: Mapped[str | None] = mapped_column(default=None)
    user_name: Mapped[str | None] = mapped_column(default=None)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
