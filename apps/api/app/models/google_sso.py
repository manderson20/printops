import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GoogleSsoSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, created/updated via the
    Integrations UI, same pattern as Mosyle/ClassGuard/GoogleWorkspaceSettings.
    Unlike those (per-job data sources), this one gates who can log into
    PrintOps at all — see app/routers/auth.py's /auth/google/* routes.

    A separate OAuth "Web application" client from the service account used
    for ChromeOS device sync in app/integrations/google_workspace.py — a
    different Google Cloud credential, different auth flow."""

    __tablename__ = "google_sso_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    client_id: Mapped[str | None] = mapped_column(default=None)
    client_secret_encrypted: Mapped[str | None] = mapped_column(default=None)
    # The Workspace "hd" (hosted domain) claim on a signed-in user's
    # id_token must match this or sign-in is rejected — this, not just "is
    # a valid Google account," is what actually restricts SSO to this
    # org's accounts.
    workspace_domain: Mapped[str | None] = mapped_column(default=None)
    # Comma-separated emails that become "admin" on first SSO sign-in;
    # everyone else starts as "viewer" — see app/routers/auth.py.
    initial_admin_emails: Mapped[str | None] = mapped_column(default=None)
    # Must exactly match the redirect URI registered for this client in
    # Google Cloud Console (e.g. https://print.example.org) — can't be
    # reliably derived from the incoming request since uvicorn sits behind
    # a reverse proxy (Caddy) that doesn't forward the original scheme.
    redirect_base_url: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
