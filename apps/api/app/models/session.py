import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SessionSettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as QuotaSettings) — the
    admin-configurable idle-timeout window for logged-in sessions.

    Sessions here are still plain stateless JWTs (app/core/security.py),
    not a server-side session store — "idle" is implemented by the
    frontend only calling POST /auth/refresh (app/routers/auth.py) while
    there's been actual mouse/keyboard activity (apps/web/src/lib/
    idleRefresh.ts), reissuing the token with a renewed exp each time. A
    user who stops interacting just lets their last-issued token's exp
    lapse on its own — no new "last seen" state needed server-side.

    Default (60) matches the previous flat, non-configurable
    Settings.jwt_expires_minutes this replaces as the source of truth for
    token lifetime — that env-var setting still exists as an unused
    historical default, but every login/refresh now reads this instead."""

    __tablename__ = "session_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    idle_timeout_minutes: Mapped[int] = mapped_column(default=60, server_default="60")
