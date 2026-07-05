import uuid

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AttributionAlias(Base, TimestampMixin):
    """Maps an arbitrary login string — a bare local username (e.g. a
    Mac's short account name "matt") or an alternate/alias email address —
    to one canonical staff email, for print-job attribution
    (app/attribution/resolve.py). Two independent sources populate this:

    - "manual": an admin explicitly merges an identity via the UI — e.g.
      "matt" always means manderson@district.org regardless of which
      device it's seen from, or a personal/old email occasionally used.
      Persists until an admin removes it; never touched by sync.
    - "google_workspace_sync": Google Workspace's own account aliases
      (the `aliases` field on a Directory API user — this is exactly
      what Google itself populates when an account's primary address is
      renamed, so a rename is picked up automatically). Wholesale
      replaced every sync (app/integrations/google_workspace.py), same
      lifecycle as GoogleWorkspaceUser/GoogleWorkspaceDevice — never
      hand-edited.

    This is deliberately separate from DeviceUserOverride (MAC-address
    scoped — "this specific physical device belongs to X") — this table
    is identity-scoped ("this login string always means X", independent
    of which device it came from)."""

    __tablename__ = "attribution_aliases"
    __table_args__ = (Index("ix_attribution_aliases_source", "source"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    alias: Mapped[str] = mapped_column(index=True, unique=True)
    resolved_email: Mapped[str] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(default="manual", server_default="manual")
    note: Mapped[str | None] = mapped_column(default=None)
