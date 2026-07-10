import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ZabbixSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, created/updated via the
    Integrations UI, same pattern as Mosyle/ClassGuard/GoogleSsoSettings.

    Unlike those (PrintOps calling out to a third party), this is the
    reverse direction: an external Zabbix server polls PrintOps over HTTP
    (see app/routers/zabbix_integration.py) using api_token, checked by
    app.deps.verify_zabbix_token — a dependency deliberately separate from
    the regular admin JWT/backend-token auth, scoped to nothing but the
    handful of read-only endpoints under /api/v1/integrations/zabbix.

    api_token is plaintext (not encrypted/hashed) — same convention as
    Printer.release_token (app/models/printer.py): a rotatable capability
    token meant to be displayed/copied in full from the UI, not a login
    secret, so it's never masked and never needs decrypting."""

    __tablename__ = "zabbix_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    api_token: Mapped[str | None] = mapped_column(unique=True, default=None)
    # Prefilled client-side from window.location.origin but stored/editable
    # (same reasoning as GoogleSsoSettings.redirect_base_url) — the address
    # a separate Zabbix server needs to reach over the network may not be
    # the same one the admin's own browser session is using.
    base_url: Mapped[str | None] = mapped_column(default=None)
