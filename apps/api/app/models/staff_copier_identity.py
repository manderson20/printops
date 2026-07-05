import uuid

from sqlalchemy import ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class StaffCopierIdentity(Base, TimestampMixin):
    """One external copier-side identity (staff ID, PIN, badge/card ID,
    department ID, vendor user code, or email) belonging to a staff
    member. A person has MANY of these, all joined only by staff_email —
    no hard FK to GoogleWorkspaceUser, matching how Job.submitted_by/
    app/attribution/resolve.py already treat email as a loose join key
    rather than a foreign-keyed user table (see app/models/google_workspace.py's
    GoogleWorkspaceUser, the canonical staff roster).

    No DB-level uniqueness on (identity_type, identity_value,
    mfp_device_id): NULL doesn't reliably constrain duplicates across
    SQLite (tests) and Postgres (production) the same way, so that check
    is done at the application layer on create instead (app/routers/
    staff_copier_identities.py), same validate-then-write style as
    app/routers/device_overrides.py:set_device_override."""

    __tablename__ = "staff_copier_identities"
    __table_args__ = (
        Index("ix_staff_copier_identities_type_value", "identity_type", "identity_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    staff_email: Mapped[str] = mapped_column(index=True)

    # staff_id | pin | badge_id | department_id | user_code |
    # vendor_user_id | email — plain string, not DB-enforced, so a new
    # identity_type can be added without a migration; Pydantic schemas
    # restrict it to a Literal.
    identity_type: Mapped[str] = mapped_column(index=True)
    identity_value: Mapped[str] = mapped_column(index=True)

    # Null = this identity is valid org-wide (e.g. a badge ID). Set = it's
    # only meaningful on this one device (e.g. a Department ID configured
    # locally on a single Canon MFP).
    mfp_device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("mfp_devices.id", ondelete="CASCADE"), index=True, default=None
    )

    # "manual" (admin-created via the UI) or "google_workspace_sync"
    # (auto-created from GoogleWorkspaceUser.employee_id when
    # GoogleWorkspaceSettings.auto_create_copier_identity_from_employee_id
    # is enabled — see app/integrations/google_workspace.py). Sync only
    # upserts its own source's rows, never touches manual ones.
    source: Mapped[str] = mapped_column(default="manual", server_default="manual")

    note: Mapped[str | None] = mapped_column(default=None)
