import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ServerSettings(Base, TimestampMixin):
    """Effectively a singleton — one row, same pattern as
    SnmpDefaultsSettings/ZabbixSettings. Config for the CUPS server's own
    client-facing identity (hostname, TLS) — distinct from Printer.use_tls,
    which is the proxy->real-printer leg. See scripts/sync_server_settings.sh
    for how a change here actually reaches cupsd.conf/Avahi; PUT
    /settings/server triggers it non-fatally (app/core/server_sync.py),
    same as printer edits trigger _apply_queue_sync.

    hostname is seeded from Settings.print_server_host (app/core/config.py)
    on first creation but is the editable, DB-backed source of truth from
    then on — changing it doesn't retroactively affect already-configured
    client print queues, only what new MDM connection info/cupsd.conf
    ServerAlias point at going forward.

    require_encryption/advertise_ipps are deliberately separate, default-off
    toggles from the hostname/cert sync itself — getting CUPS a real,
    trusted certificate (instead of its auto-generated self-signed one) is
    a pure improvement with no failure mode for existing plaintext clients,
    but *requiring* TLS or advertising the secure Bonjour variant are the
    two changes that could actually break an unusual client, so both stay
    explicit opt-ins."""

    __tablename__ = "server_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    hostname: Mapped[str] = mapped_column(default="", server_default="")
    require_encryption: Mapped[bool] = mapped_column(default=False, server_default="false")
    advertise_ipps: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Non-fatal — set when scripts/sync_server_settings.sh fails, same
    # convention as Printer.queue_sync_error. None means the last sync (or
    # the daily infra/cert-sync timer) succeeded, or none has run yet.
    sync_error: Mapped[str | None] = mapped_column(default=None)
