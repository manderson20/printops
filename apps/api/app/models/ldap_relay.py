import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LdapRelaySettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as SnmpDefaultsSettings/QuotaSettings)
    — org-wide config for the LDAP address-book relay (infra/ldap-relay/),
    which lets copiers do scan-to-email address-book lookups against
    PrintOps's already-synced Google Workspace roster instead of each
    copier holding its own direct LDAP connection to Google.

    `enabled` defaults false, matching every other settings model that
    exposes something new to the network (SNMP, print-release) — nothing
    listens/serves until an admin opts in. Plain LDAP (not LDAPS) by
    design for now: matches this system's existing security posture, where
    IPP itself is optional-TLS per printer on a trusted internal network
    (see Printer.use_tls) — bind credentials do traverse in cleartext on
    that network as a result, a known and explicit trade-off, not an
    oversight."""

    __tablename__ = "ldap_relay_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    # e.g. "dc=example,dc=org" — admin-chosen, shared across every
    # printer's LDAP address-book config so they can all point at the same
    # base DN. Entries are served at "ou=people,<base_dn>".
    base_dn: Mapped[str] = mapped_column(
        default="dc=printops,dc=local", server_default="dc=printops,dc=local"
    )
    port: Mapped[int] = mapped_column(default=389, server_default="389")
