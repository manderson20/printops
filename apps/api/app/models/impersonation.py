import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImpersonationSession(Base, TimestampMixin):
    """One row per admin "View as" click (app/routers/users.py's
    impersonate_user) — an append-only audit trail, never updated or
    deleted, since impersonation is stateless (a short-lived JWT, see
    that endpoint's docstring) and has no other server-side session to
    tie a log row to. created_at (from TimestampMixin) is the start time;
    there's no explicit end time because there's no signal for one — the
    admin closing the banner client-side just discards the token, and an
    unattended one simply expires at expires_at, same as any other JWT."""

    __tablename__ = "impersonation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Null for the dev break-glass admin account (app/routers/auth.py's
    # /auth/login) — it has no User row at all (see User's docstring), so
    # there's nothing to point a FK at. admin_email is always populated
    # regardless, so the audit trail never loses who did this.
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, default=None
    )
    # Denormalized alongside the FK — kept even if the admin account is
    # later deleted/renamed, so the audit trail still reads sensibly.
    admin_email: Mapped[str]

    target_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_email: Mapped[str]
    target_role: Mapped[str]

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
