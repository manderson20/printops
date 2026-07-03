import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UpdateSchedule(Base, TimestampMixin):
    """A requested (or completed/failed) software update — at most one row
    is ever "pending" or "in_progress" at a time (enforced in
    app/routers/updates.py, not a DB constraint, to keep the "already
    scheduled, cancel it first" UX simple). The actual pull/migrate/
    rebuild/restart never runs inside the API process itself — restarting
    printops-api.service mid-request would kill the request handling its
    own completion report — so this table is really a mailbox between the
    admin-facing API (writes a schedule, admin-only) and the host-level
    systemd timer in infra/update-watcher/ (polls for a due row via the
    same X-Backend-Token trust boundary as the CUPS backend script, then
    reports status/log back once it's run infra/update-watcher/apply-update.sh)."""

    __tablename__ = "update_schedule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    target_version: Mapped[str] = mapped_column()
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # "pending" -> "in_progress" -> "completed" | "failed". A cancelled
    # schedule is just marked "failed" with an explanatory log rather than
    # deleted, so update history stays complete.
    status: Mapped[str] = mapped_column(default="pending", server_default="pending")
    log: Mapped[str | None] = mapped_column(default=None)
    requested_by: Mapped[str | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
