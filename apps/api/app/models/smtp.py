import uuid

from sqlalchemy import Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SmtpSettings(Base, TimestampMixin):
    """How to send mail. Singleton, same pattern as SyslogSettings.

    Deliberately its own model rather than columns on NotificationSettings.
    "Scheduled/emailed report delivery" is a separate item on the roadmap and
    will want exactly this — one relay, configured once, whatever is being
    sent. Notifications happen to be the first thing that needs it.

    `enabled` defaults false like every settings model that reaches outside
    this box: a half-configured relay should refuse to send rather than fail
    per message.
    """

    __tablename__ = "smtp_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    host: Mapped[str] = mapped_column(String, default="", server_default="")
    # 587 (submission with STARTTLS) rather than 25: this district relays
    # through Google Workspace, and 25 is both blocked outbound by most ISPs
    # and the port where nobody expects authentication.
    port: Mapped[int] = mapped_column(Integer, default=587, server_default="587")

    username: Mapped[str] = mapped_column(String, default="", server_default="")
    # Encrypted at rest via app/core/crypto.py, the same treatment as the SNMP
    # community and the LDAP bind password. Never returned by the API — the
    # settings endpoint reports whether one is set, not what it is.
    password_encrypted: Mapped[str | None] = mapped_column(String, default=None)

    from_address: Mapped[str] = mapped_column(String, default="", server_default="")
    # Optional display name. "PrintOps <printops@district.org>" reads better in
    # a crowded inbox than a bare address, and an alert nobody recognises is an
    # alert nobody opens.
    from_name: Mapped[str] = mapped_column(String, default="PrintOps", server_default="PrintOps")

    # STARTTLS on the submission port, which is what Google Workspace and every
    # other relay worth using expects. Off means plaintext, and is offered only
    # because an on-premises relay on a trusted VLAN is a real deployment.
    use_starttls: Mapped[bool] = mapped_column(default=True, server_default="true")
