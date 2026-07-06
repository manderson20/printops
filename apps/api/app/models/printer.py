import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Printer(Base, TimestampMixin):
    __tablename__ = "printers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Not yet exposed via the API — reserved so multi-tenancy doesn't require a
    # retrofit later (see ARCHITECTURE.md §6).
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    name: Mapped[str]
    manufacturer: Mapped[str | None] = mapped_column(default=None)
    model: Mapped[str | None] = mapped_column(default=None)

    ip_address: Mapped[str]
    hostname: Mapped[str | None] = mapped_column(default=None)
    serial_number: Mapped[str | None] = mapped_column(default=None)

    port: Mapped[int] = mapped_column(default=631, server_default="631")
    use_tls: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Optional override for the IPP resource path (e.g. "/printers/queue-name")
    # when the default candidate-path probing doesn't find the printer.
    ipp_path: Mapped[str | None] = mapped_column(default=None)

    # Controls whether the CUPS queue is advertised via mDNS/Bonjour (AirPrint
    # discovery) — off by default so a newly-added printer isn't visible to
    # every device on the subnet until an admin explicitly opts in. See
    # ARCHITECTURE.md §4; scripts/sync_cups_queue.sh maps this to CUPS's
    # printer-is-shared attribute.
    airprint_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")

    building: Mapped[str | None] = mapped_column(default=None)
    room: Mapped[str | None] = mapped_column(default=None)
    department: Mapped[str | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(default=None)

    capabilities: Mapped[dict | None] = mapped_column(JSON, default=None)
    capabilities_raw: Mapped[dict | None] = mapped_column(JSON, default=None)
    capabilities_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    capabilities_error: Mapped[str | None] = mapped_column(default=None)

    # Set when scripts/sync_cups_queue.sh fails to create/update this
    # printer's CUPS queue (e.g. printer unreachable during the -m everywhere
    # probe). None means the queue is in sync as of the last create/update.
    queue_sync_error: Mapped[str | None] = mapped_column(default=None)

    # Reachability/error state, refreshed by app/printers/status.py — either
    # on the 60s background poll (app/main.py) or a manual "Check Now" call
    # (POST /printers/{id}/check-status). "unknown" until the first check.
    # See app/printers/status.py:derive_status for how state maps to these.
    status: Mapped[str] = mapped_column(default="unknown", server_default="unknown")
    # Raw IPP printer-state-reasons (e.g. ["media-jam-error"]), excluding the
    # no-op "none" value. Empty/None when status isn't "error".
    status_reasons: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Human-readable detail: the printer's own printer-state-message, or the
    # probe failure reason when status is "offline".
    status_message: Mapped[str | None] = mapped_column(default=None)
    status_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Print-and-release. When true, the CUPS backend script
    # (infra/cups/backends/printops) holds every job sent to this printer's
    # queue instead of forwarding it immediately — see app/routers/release.py
    # and app/printers/release.py for how a held job is later delivered via
    # a second, internal-only CUPS queue (scripts/sync_release_queue.sh).
    release_required: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Opaque, unguessable — identifies this printer in the public kiosk URL
    # (/release/<token>) instead of exposing the raw printer id. Regenerable
    # by an admin (e.g. a lost/reissued kiosk iPad) without needing to
    # rebuild the printer itself.
    release_token: Mapped[str | None] = mapped_column(unique=True, default=None)

    # SNMP page/copy counter polling (app/printers/snmp_counters.py). All
    # connection-config fields are nullable — None means "use the global
    # SnmpDefaultsSettings", letting a district set one community string
    # once instead of per printer, with an override for the odd device
    # configured differently.
    snmp_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    snmp_port: Mapped[int | None] = mapped_column(default=None)
    snmp_version: Mapped[str | None] = mapped_column(default=None)  # "v1" | "v2c"
    snmp_community_encrypted: Mapped[str | None] = mapped_column(default=None)
    # Manual override for detect_vendor_profile()'s heuristic — needed for
    # OEM-rebadged hardware (e.g. a Kyocera engine sold as Copystar) the
    # manufacturer/model text won't match. None = auto-detect.
    snmp_vendor_profile: Mapped[str | None] = mapped_column(default=None)

    # Poll results. On a failed probe, page_count_error is set but the last
    # known-good counts are left in place (same convention as
    # capabilities_error not wiping capabilities) — a transient SNMP hiccup
    # shouldn't erase yesterday's good reading.
    page_count_total: Mapped[int | None] = mapped_column(default=None)
    page_count_copy: Mapped[int | None] = mapped_column(default=None)
    page_count_print: Mapped[int | None] = mapped_column(default=None)
    # "verified" (Canon's self-describing MIB, cross-checked against the
    # total) | "best_effort" (numerically consistent but unlabeled, e.g.
    # Konica Minolta) | "unsupported" (total only — unrecognized vendor, or
    # a vendor with no confirmed breakdown yet).
    page_count_confidence: Mapped[str | None] = mapped_column(default=None)
    page_count_vendor_profile_used: Mapped[str | None] = mapped_column(default=None)
    page_count_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    page_count_error: Mapped[str | None] = mapped_column(default=None)

    # LDAP address-book relay (infra/ldap-relay/, app/routers/internal.py's
    # /ldap/bind + /ldap/search) — lets this printer's scan-to-email address
    # book search PrintOps instead of holding its own direct connection to
    # Google Workspace. ldap_bind_username is a short admin-chosen login
    # name (plaintext — not itself secret, like a username), matched
    # case-insensitively against whatever bind identifier the copier sends
    # (vendors format the LDAP "username"/DN field differently, so this
    # isn't a strict full-DN comparison). The password is hashed (bcrypt via
    # app/core/security.py's hash_password/verify_password — the same
    # scheme the local admin login uses), not encrypted like
    # snmp_community_encrypted: PrintOps only ever needs to *verify* a bind
    # attempt, never reproduce the plaintext, so a one-way hash is the
    # stronger choice here. Off by default per printer, same as
    # LdapRelaySettings.enabled is off by default org-wide — both gates
    # must be on for this printer's address book to actually work.
    ldap_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    ldap_bind_username: Mapped[str | None] = mapped_column(unique=True, default=None)
    ldap_bind_password_hash: Mapped[str | None] = mapped_column(default=None)

    @property
    def has_snmp_community(self) -> bool:
        """Masks snmp_community_encrypted for PrinterOut — a plain property
        (not a column) so Pydantic's from_attributes=True picks it up via
        getattr the same way it reads a real column, matching how routers
        just `return printer` and let FastAPI do the ORM->schema mapping."""
        return bool(self.snmp_community_encrypted)

    @property
    def has_ldap_bind_password(self) -> bool:
        """Masks ldap_bind_password_hash for PrinterOut — same masking
        pattern as has_snmp_community above."""
        return bool(self.ldap_bind_password_hash)
