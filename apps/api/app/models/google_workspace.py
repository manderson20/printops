import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GoogleWorkspaceSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, created/updated via the
    Integrations UI. Auth is a service account + domain-wide delegation
    (not a simple token/password): the whole service-account JSON key is
    stored encrypted, plus the Workspace admin email to impersonate (the
    JWT "sub" claim) — see app/integrations/google_workspace.py."""

    __tablename__ = "google_workspace_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # The full service-account JSON key file content (client_email,
    # private_key, etc.), encrypted at rest as one blob.
    service_account_json_encrypted: Mapped[str | None] = mapped_column(default=None)
    # Workspace admin/user to impersonate via domain-wide delegation —
    # needs directory-read permission. Not a secret itself, but only
    # meaningful alongside the key above.
    admin_email: Mapped[str | None] = mapped_column(default=None)
    # "my_customer" is Google's own alias for "the customer that owns this
    # domain" — almost never needs to be a real customer ID.
    customer_id: Mapped[str] = mapped_column(default="my_customer")
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_error: Mapped[str | None] = mapped_column(default=None)
    device_count: Mapped[int] = mapped_column(default=0, server_default="0")


class GoogleWorkspaceDevice(Base):
    """Local cache of Workspace's ChromeOS device inventory, refreshed
    periodically by app/integrations/google_workspace.py's sync_devices —
    never queried live per print job (see app/attribution/resolve.py)."""

    __tablename__ = "google_workspace_devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    mac_address: Mapped[str] = mapped_column(index=True, unique=True)
    serial_number: Mapped[str | None] = mapped_column(default=None)
    device_name: Mapped[str | None] = mapped_column(default=None)
    user_email: Mapped[str | None] = mapped_column(default=None)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GoogleWorkspaceUser(Base):
    """Local cache of the org's full Workspace user directory (not just
    device-assigned users), refreshed by sync_users alongside the device
    sync. This is the canonical identity roster used to validate device
    override emails (app/models/device_override.py) and to disambiguate
    a bare, non-unique local username (e.g. two different people's Mac
    accounts both named "matt") — see app/attribution/resolve.py."""

    __tablename__ = "google_workspace_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    email: Mapped[str] = mapped_column(index=True, unique=True)
    name: Mapped[str | None] = mapped_column(default=None)
    # Google's built-in Employee ID field (externalIds[type=organization] —
    # see app/integrations/google_workspace.py:extract_employee_id). Feeds
    # the copier PIN roster export (app/routers/settings.py); null for
    # anyone without one set in their Workspace profile.
    employee_id: Mapped[str | None] = mapped_column(default=None)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
