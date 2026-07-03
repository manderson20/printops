import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DeviceUserOverride(Base, TimestampMixin):
    """Admin-set correction of a device's attributed user, keyed by MAC
    address — the one identifier that unambiguously names a single
    physical device even when Mosyle/Google Workspace's own reported
    username is missing, stale, or (e.g. a bare first name shared by
    multiple people) not unique. Checked first in the attribution chain,
    ahead of Mosyle/Google Workspace's own device record — see
    app/attribution/resolve.py. resolved_email is validated against the
    GoogleWorkspaceUser roster at write time (app/routers/device_overrides.py)
    so it can't drift from a real, canonical address."""

    __tablename__ = "device_user_overrides"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    mac_address: Mapped[str] = mapped_column(index=True, unique=True)
    resolved_email: Mapped[str] = mapped_column()
    note: Mapped[str | None] = mapped_column(default=None)
