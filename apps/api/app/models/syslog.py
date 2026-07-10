import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# RFC 5424 severities (also what a device speaking older RFC 3164 encodes in
# its <PRI> tag — 3164 reuses 5424's severity numbering, it just formats the
# rest of the message differently). Ordered highest-priority first; used to
# implement SyslogSettings.min_severity as a floor ("emerg" through this
# level are kept), not an allowlist of specific levels.
SEVERITY_ORDER = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


class SyslogSettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as SnmpDefaultsSettings) — org-wide
    config for the syslog collector (infra/syslog-relay/). Unlike SNMP,
    there's no per-printer override table: PrintOps doesn't poll anything
    here, a device either has been configured (on the device's own admin
    UI) to send syslog at this host or it hasn't, so there's nothing to
    toggle per-printer on PrintOps' side.

    `enabled` defaults false, matching every other settings model that
    depends on external device config (Mosyle, SNMP defaults, LDAP relay)
    — the relay service listens regardless (see infra/syslog-relay/server.py
    main()), but ingestion no-ops until an admin opts in here, same
    convention as the LDAP relay.

    `min_severity` is a noise floor, not a display filter — raw device
    syslog is chatty (every job, every state poll on some vendors), so
    events below this level are dropped at ingest time rather than stored
    and filtered later. Defaults to "warning": routine info/debug/notice
    traffic isn't useful for "is there an error to diagnose" and would
    otherwise dominate retention_days of storage."""

    __tablename__ = "syslog_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    port: Mapped[int] = mapped_column(Integer, default=514, server_default="514")
    min_severity: Mapped[str] = mapped_column(String, default="warning", server_default="warning")
    # Governs the purge loop (app/main.py:_syslog_event_purge_loop). Shorter
    # default than SNMP's counter-reading retention (180 days) — syslog
    # volume per event is higher and the diagnostic value of a months-old
    # event is low.
    retention_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")


class PrinterSyslogEvent(Base):
    """An append-only log of syslog messages received from printers,
    parsed by infra/syslog-relay/server.py and posted to POST
    /api/v1/internal/syslog/events. Modeled on PrinterCounterReading
    (app/models/snmp.py) — no TimestampMixin, since received_at (when the
    relay actually got the UDP packet) is the only timestamp that matters
    for an append-only history.

    printer_id is nullable and source_ip is always stored: a packet whose
    source address doesn't match any known Printer.ip_address is still
    kept (with printer_id left null) rather than dropped, so an admin can
    notice "something is sending syslog here that isn't registered yet"
    instead of it silently vanishing."""

    __tablename__ = "printer_syslog_events"
    __table_args__ = (
        Index("ix_printer_syslog_events_printer_received", "printer_id", "received_at"),
        Index("ix_printer_syslog_events_received_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    printer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="CASCADE"), index=True, default=None
    )
    source_ip: Mapped[str] = mapped_column(String)

    # When the relay received the UDP packet — always set, always this
    # host's clock. device_timestamp is the printer's own clock, from
    # inside the syslog message itself, and is frequently absent/wrong
    # (many MFPs don't NTP-sync or send garbage timestamps), so
    # received_at (not device_timestamp) is what ordering/retention/the
    # printer_received index are built on.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    device_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # 0-7, RFC 5424 numbering (SEVERITY_ORDER above) — None only for a
    # message the relay couldn't parse a <PRI> tag out of at all.
    severity: Mapped[str | None] = mapped_column(String, default=None)
    facility: Mapped[int | None] = mapped_column(Integer, default=None)
    hostname: Mapped[str | None] = mapped_column(String, default=None)
    app_name: Mapped[str | None] = mapped_column(String, default=None)
    message: Mapped[str] = mapped_column(Text)
    # The full, unparsed line — kept so a parsing bug or an unrecognized
    # vendor format never loses information, even when the structured
    # fields above are wrong or missing.
    raw: Mapped[str] = mapped_column(Text)
