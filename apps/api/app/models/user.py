import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """A PrintOps account. Two ways rows get here: Google SSO login
    (app/routers/auth.py's /auth/google/callback, lazy-provisioned on
    first sign-in) and Google Workspace directory sync
    (app/integrations/google_workspace.py's sync_users, proactive roster
    pull). Both key off google_sub — the OIDC `sub` claim and the
    Directory API's `id` field are the same value — so a directory-synced
    row and a later SSO login resolve to one record instead of two.

    The pre-existing dev username/password stub (app/routers/auth.py's
    /auth/login) is intentionally NOT backed by this table — it remains a
    single hardcoded account outside the User model, see get_current_user
    in app/deps.py."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    email: Mapped[str] = mapped_column(unique=True, index=True)
    name: Mapped[str | None] = mapped_column(default=None)
    picture_url: Mapped[str | None] = mapped_column(default=None)

    # Null for any account not sourced from Google (none exist yet, but
    # keeps the door open for other IdPs per ARCHITECTURE.md's Auth & RBAC
    # section). Unique since it's Google's stable per-account identifier.
    google_sub: Mapped[str | None] = mapped_column(unique=True, index=True, default=None)

    # "admin", "viewer", or "ou_viewer" — enforced via app.deps.require_role.
    # New SSO accounts default to "viewer" unless their email is in
    # settings.initial_admin_emails (see app/routers/auth.py's callback).
    # "ou_viewer" is a read-only account scoped to Insights only, filtered
    # to granted_ou_paths below — see app/routers/reports.py's
    # _report_filters for the enforcement.
    role: Mapped[str] = mapped_column(default="viewer", server_default="viewer")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    # Google Workspace OU paths (e.g. "/Schools/Elementary/BuildingA") this
    # "ou_viewer" account can see in Insights — matched against
    # GoogleWorkspaceUser.org_unit_path via app.integrations.google_workspace
    # .org_unit_matches, which also covers everything nested under a granted
    # path. Ignored for "admin"/"viewer" roles. Null/empty means "sees
    # nothing yet" — a safe default rather than showing everyone before an
    # admin configures it.
    granted_ou_paths: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Opts this account out of the idle timeout entirely (app/routers/
    # auth.py's /auth/refresh mints a long-lived token instead of the
    # admin-configured SessionSettings.idle_timeout_minutes for it) — e.g.
    # a front-desk/shared login that should just stay signed in all day.
    # Checked fresh from this row on every refresh, not baked into the
    # JWT at login, so revoking it takes effect on the user's very next
    # refresh instead of waiting for their token to expire.
    exempt_from_timeout: Mapped[bool] = mapped_column(default=False, server_default="false")

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
