import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CopierProvisionedAccount(Base, TimestampMixin):
    """A record that PrintOps put a specific person's login onto a specific
    copier.

    Exists because the device can't tell us. A bizhub reports only whether
    an account has a password (TrackPasswordExist is a boolean), never what
    that password is — so there is no way to look at a copier and work out
    which accounts correspond to which staff. Without this table a second
    sync re-adds everyone and the device rejects every one of them as a
    duplicate password, which is exactly what happened the first time.

    It is also the join that makes usage attributable: a device reports
    activity against an account number, and this is what maps that number
    back to a person.

    removed_at rather than deleting the row: when someone leaves, their
    historic usage must stay attributable to them, and a later person
    reusing the same email or code must not inherit it. Keeping a closed
    record with an end date is what lets a reused identifier resolve to the
    right person by date instead of silently crossing the two over.
    """

    __tablename__ = "copier_provisioned_accounts"
    __table_args__ = (
        Index(
            "ix_copier_provisioned_device_identity",
            "mfp_device_id",
            "identity_value",
        ),
        Index("ix_copier_provisioned_device_account", "mfp_device_id", "device_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    mfp_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True
    )

    # The person, as of provisioning time. Both are stored rather than
    # joined live: an account on a device is a historical fact, and it must
    # stay readable even after the identity row or the directory user is
    # gone — which is the whole point of removed_at below.
    staff_email: Mapped[str] = mapped_column(index=True)
    identity_value: Mapped[str] = mapped_column(index=True)
    identity_type: Mapped[str] = mapped_column(default="staff_id", server_default="staff_id")

    # The device's own account number/id, so usage reported against it can
    # be resolved back to a person.
    device_account_id: Mapped[str]
    # What was written into the device's (8-character) name field, kept so
    # an admin can match what they see on the copier to a person.
    device_account_name: Mapped[str | None] = mapped_column(default=None)

    provisioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Null = still on the device. Set = PrintOps no longer considers this
    # account that person's; historic usage before this timestamp stays
    # theirs.
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )


class CopierSyncJob(Base, TimestampMixin):
    """One run of "push staff accounts to this copier", tracked so it can
    report progress while it happens.

    A sync is minutes long — one HTTP round trip per account, against a
    device that allows a single admin session — so it cannot be a blocking
    request. Without a record like this the UI can only show a spinner, and
    an admin genuinely cannot tell a long sync from a hung one. (It could
    not, and that is why this exists.)

    Rows are kept after completion rather than deleted: "what did the last
    sync actually do" is the first question asked when something looks
    wrong, and the answer should outlive the browser tab it ran in.
    """

    __tablename__ = "copier_sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mfp_device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True
    )

    # pending | running | succeeded | failed
    status: Mapped[str] = mapped_column(default="pending", server_default="pending", index=True)
    # How the run was started, so an unexpected change on a shared device
    # can be traced to a person or to the schedule.
    trigger: Mapped[str] = mapped_column(default="manual", server_default="manual")

    total: Mapped[int] = mapped_column(default=0, server_default="0")
    completed: Mapped[int] = mapped_column(default=0, server_default="0")
    failed: Mapped[int] = mapped_column(default=0, server_default="0")
    # Already on the device from a previous run — counted separately from
    # failures, because "nothing to do" is a success, not an error.
    skipped: Mapped[int] = mapped_column(default=0, server_default="0")

    message: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
