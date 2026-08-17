import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CopierAccountCounterReading(Base, TimestampMixin):
    """The last known value of one copier account's lifetime counters.

    A copier's Account Track counters only ever climb — they are meters,
    not a log of activity. Reading them once tells you an account's entire
    history since the device was installed; usage only exists as the
    difference between two readings. This table holds the readings that
    difference is taken against (app/copiers/account_counters.py), and
    CopierUsageRecord holds the usage that falls out of it.

    Run-length encoded rather than one row per poll: `first_seen_at` is
    when this set of values first appeared and `last_seen_at` is the most
    recent poll that still saw them, so an idle account costs one row
    forever instead of one row per poll. That also makes the usage window
    honest — when a value finally changes, the pages were produced between
    the previous reading's last_seen_at and now, which is a tighter and
    truer window than "sometime since the value last changed".

    `superseded_at` marks a reading that has been diffed and replaced. The
    current reading for an account is the one row where it is null.
    """

    __tablename__ = "copier_account_counter_readings"
    __table_args__ = (
        Index(
            "ix_copier_counter_readings_current",
            "mfp_device_id",
            "device_account_id",
            "superseded_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    mfp_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True
    )
    # The device's own account number — the only identifier the counter
    # read returns. CopierProvisionedAccount is what maps it to a person.
    device_account_id: Mapped[str] = mapped_column(index=True)

    # {activity: {counter_type: value}}, exactly as the device reported it.
    # Stored whole rather than as named columns because the counter types a
    # model reports vary (a mono bizhub has no FullColor), and a reading
    # that silently dropped a type would produce wrong deltas forever after.
    counters: Mapped[dict] = mapped_column(JSON, default=dict)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )

    # True when this reading came in below the one before it, which on a
    # monotonic counter means the account's counters were cleared on the
    # device (or the account number was reused). Kept as a flag rather than
    # thrown away: it explains a usage row whose numbers look odd, and it
    # is the only trace that a clear ever happened.
    follows_counter_reset: Mapped[bool] = mapped_column(default=False, server_default="false")
