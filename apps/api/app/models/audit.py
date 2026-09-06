import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Below this, the retention window stops being an audit trail. Enforced in the
# schema (app/schemas/audit.py) rather than here so the API rejects it with a
# useful message, but the number lives beside the column it constrains.
MIN_AUDIT_RETENTION_DAYS = 90


class AuditSettings(Base, TimestampMixin):
    """Singleton (one row, same pattern as SyslogSettings/SnmpDefaultsSettings)
    — org-wide config for how long the audit trail is kept.

    No `enabled` flag, deliberately, unlike every other settings model here.
    Those gate something that talks to the network and default to off until an
    admin opts in; this one records what admins do, and a switch to stop
    recording is a switch to stop being auditable. If auditing should ever be
    optional that is a product decision, not a checkbox someone finds.
    """

    __tablename__ = "audit_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # 400 days: a full school year plus a margin, so "what did we change last
    # August" is still answerable in September. Governs the purge loop in
    # app/main.py, alongside the syslog and SNMP ones. Floored at
    # MIN_AUDIT_RETENTION_DAYS — a log an admin can set to one day is weaker
    # evidence than one they cannot.
    retention_days: Mapped[int] = mapped_column(Integer, default=400, server_default="400")


class AuditEvent(Base):
    """One admin action. Append-only: never updated, never deleted except by
    the retention purge.

    Written by app/audit/record.py's record_audit() into the *endpoint's own*
    session, so the event and the change it describes commit together or not at
    all. That is the whole design — see ARCHITECTURE.md §10. A log that can
    record a change that did not happen, or miss one that did, is not an audit
    log, and nothing weaker than a shared transaction gives that.

    No TimestampMixin: `occurred_at` is the only time that matters and an
    append-only row has no meaningful updated_at. Same reasoning as
    PrinterSyslogEvent and PrinterCounterReading.

    Scope is admin and config actions. Service-to-service traffic (the CUPS
    backend's per-job POST/PATCH, Zabbix polling) and ordinary user actions are
    deliberately absent: the first would bury the second in thousands of rows a
    week nobody reads, and job history already lives on Job rows.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Null for the dev break-glass admin account, which has no User row at all
    # (see User's docstring) — the same situation ImpersonationSession handles.
    #
    # SET NULL, not CASCADE. ImpersonationSession uses CASCADE, which means
    # deleting a user destroys the record of them having been impersonated;
    # that is wrong for anything calling itself an audit trail and is not
    # copied here. Deleting an admin must not delete the evidence of what they
    # did. actor_email below is what the row is actually read by.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    # Always populated, denormalized on purpose: the trail has to stay readable
    # after an account is renamed or deleted.
    actor_email: Mapped[str] = mapped_column(String)
    actor_role: Mapped[str] = mapped_column(String)
    # Set when an admin was acting through "View as" (UserOut.impersonated_by),
    # so the row says who was really at the keyboard rather than whose session
    # it looked like.
    impersonated_by: Mapped[str | None] = mapped_column(String, default=None)

    # Dotted and hierarchical — "settings.snmp.update", "printer.delete" — so
    # the UI can filter by prefix without a second category column.
    action: Mapped[str] = mapped_column(String, index=True)

    entity_type: Mapped[str | None] = mapped_column(String, default=None)
    entity_id: Mapped[str | None] = mapped_column(String, default=None)
    # The entity's name as it was at the time. Denormalized for the same reason
    # as actor_email: "Deleted printer 3f5c…" is not an answer anybody wants.
    entity_label: Mapped[str | None] = mapped_column(String, default=None)

    # One human sentence, written at the call site. The row should be readable
    # without the reader reconstructing it from `changes`.
    summary: Mapped[str] = mapped_column(String)

    # {field: {"from": ..., "to": ...}}. Secret-bearing fields are recorded as
    # changed with both values replaced — see app/audit/record.py. Null when the
    # action has no before/after (a login, a delete).
    changes: Mapped[dict | None] = mapped_column(JSON, default=None)

    source_ip: Mapped[str | None] = mapped_column(String, default=None)
