import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MfpDevice(Base, TimestampMixin):
    """A walk-up copier/MFP tracked for copier-side (non-IPP) usage
    accounting — see app/copiers/ for the connector layer that actually
    talks to (or imports data for) one of these. Deliberately separate
    from Printer: not every copier is CUPS-proxied (a device onboarded
    purely via CSV import may have no reachable IP at all), and Printer's
    fields/machinery (required ip_address, CUPS queue sync, AirPrint,
    release tokens) are meaningless for a device tracked only for
    accounting. printer_id links the two when the same physical device is
    both a CUPS-proxied printer and a walk-up copier.

    Capability booleans are all bool | None, not bool: None means "not yet
    assessed" (the honest default), never conflated with False ("confirmed
    unsupported") — same convention as Printer.page_count_confidence's
    verified/best_effort/unsupported distinction in app/printers/
    snmp_counters.py."""

    __tablename__ = "mfp_devices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(default="default", server_default="default", index=True)

    printer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("printers.id", ondelete="SET NULL"), index=True, default=None
    )

    name: Mapped[str]
    # canon | konica_minolta | hp | lexmark | kyocera | ricoh | sharp |
    # xerox | generic — enforced as a Literal only in the Pydantic schema,
    # matching how Printer.snmp_vendor_profile is stored as a plain string.
    vendor: Mapped[str] = mapped_column(default="generic", server_default="generic")
    model: Mapped[str | None] = mapped_column(default=None)
    serial_number: Mapped[str | None] = mapped_column(default=None)

    # Nullable, unlike Printer.ip_address — a CSV-only device tracked
    # purely for accounting may have no reachable address at all.
    ip_address: Mapped[str | None] = mapped_column(default=None)
    hostname: Mapped[str | None] = mapped_column(default=None)

    building: Mapped[str | None] = mapped_column(default=None)
    room: Mapped[str | None] = mapped_column(default=None)
    department: Mapped[str | None] = mapped_column(default=None)

    # Which app/copiers/ connector drives this device — "generic_csv" |
    # "generic_snmp" in Stage 1; later stages register additional
    # connector_types purely additively (see app/copiers/registry.py),
    # no schema change needed. Not DB-enforced as an enum so a connector
    # can be added without a migration.
    connector_type: Mapped[str] = mapped_column(default="generic_csv", server_default="generic_csv")
    # Reserved for connector-specific settings that don't warrant their own
    # column yet (e.g. a future vendor API base URL).
    connector_config: Mapped[dict | None] = mapped_column(JSON, default=None)

    cap_walkup_copy_accounting: Mapped[bool | None] = mapped_column(default=None)
    cap_user_code_pin_auth: Mapped[bool | None] = mapped_column(default=None)
    cap_badge_card_auth: Mapped[bool | None] = mapped_column(default=None)
    cap_department_id_accounting: Mapped[bool | None] = mapped_column(default=None)
    cap_ldap_auth: Mapped[bool | None] = mapped_column(default=None)
    cap_local_user_table: Mapped[bool | None] = mapped_column(default=None)
    cap_remote_user_provisioning: Mapped[bool | None] = mapped_column(default=None)
    cap_csv_accounting_export: Mapped[bool | None] = mapped_column(default=None)
    cap_api_accounting_retrieval: Mapped[bool | None] = mapped_column(default=None)
    cap_snmp_meter_counters: Mapped[bool | None] = mapped_column(default=None)
    cap_scan_accounting: Mapped[bool | None] = mapped_column(default=None)
    cap_color_mono_accounting: Mapped[bool | None] = mapped_column(default=None)
    cap_quotas: Mapped[bool | None] = mapped_column(default=None)
    # Stage 5 placeholder — always manually set (never connector-reported)
    # until secure print release actually extends to copiers.
    cap_secure_print_release: Mapped[bool | None] = mapped_column(default=None)

    capabilities_source: Mapped[str | None] = mapped_column(
        default=None
    )  # manual | connector_reported
    capabilities_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # SNMP meter fields, mirroring Printer's exactly (app/printers/
    # snmp_counters.py) — used by app/copiers/generic_snmp.py only when
    # printer_id is null; when set, the printer's own already-polled
    # page_count_* is read instead, to avoid polling the same OIDs twice.
    snmp_enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    snmp_port: Mapped[int | None] = mapped_column(default=None)
    snmp_version: Mapped[str | None] = mapped_column(default=None)
    snmp_community_encrypted: Mapped[str | None] = mapped_column(default=None)
    snmp_vendor_profile: Mapped[str | None] = mapped_column(default=None)
    page_count_total: Mapped[int | None] = mapped_column(default=None)
    page_count_copy: Mapped[int | None] = mapped_column(default=None)
    page_count_print: Mapped[int | None] = mapped_column(default=None)
    page_count_confidence: Mapped[str | None] = mapped_column(default=None)
    page_count_vendor_profile_used: Mapped[str | None] = mapped_column(default=None)
    page_count_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    page_count_error: Mapped[str | None] = mapped_column(default=None)

    # The device's own admin web UI login, used by connectors that actually
    # drive the device (per-user accounting pulls, user provisioning) — see
    # app/copiers/device_admin.py.
    #
    # Deliberately NOT the same thing as Printer.web_login_password_encrypted,
    # despite often holding the same secret: that field is reference-only
    # storage ("a secure place to look them up instead of a spreadsheet",
    # see Printer's docstring) and PrintOps never authenticates with it. This
    # one is a live credential PrintOps logs in with, so it belongs to the
    # device that the connector talks to and is separated from the reference
    # copy on purpose — revoking one shouldn't silently change the other.
    #
    # admin_username is nullable because several vendors' admin logins have
    # no username concept at all (a Konica bizhub prompts for a password
    # only; a Lexmark XM3350's sole login method declares
    # credentials: ["password"]) — same reasoning as
    # Printer.web_login_username.
    admin_username: Mapped[str | None] = mapped_column(default=None)
    admin_password_encrypted: Mapped[str | None] = mapped_column(default=None)

    # Which staff OUs get provisioned onto THIS device, narrowing the
    # org-wide copier-accounting OUs (GoogleWorkspaceSettings.
    # copier_identity_org_unit_paths). Null = everyone the org-wide filter
    # allows.
    #
    # Per-device rather than org-wide only, because device limits are
    # per-device: a Lexmark XM3350 holds 250 local accounts and a bizhub
    # 1000, so a district that fits comfortably org-wide can still overflow
    # one machine. Scoping a copier to its own building is also just
    # correct — the people who walk up to the elementary copier are the
    # elementary staff.
    provision_org_unit_paths: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Whether the background loop pushes staff accounts to this device on a
    # schedule. Off by default and per device, deliberately: writing login
    # accounts to a shared machine is not something to start doing to a
    # whole fleet because a global setting was flipped. An admin turns it on
    # per copier once they've seen a manual sync do the right thing.
    auto_sync_users: Mapped[bool] = mapped_column(default=False, server_default="false")
    last_user_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_user_sync_ok: Mapped[bool | None] = mapped_column(default=None)
    last_user_sync_message: Mapped[str | None] = mapped_column(default=None)

    last_test_connection_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_test_connection_ok: Mapped[bool | None] = mapped_column(default=None)
    last_test_connection_message: Mapped[str | None] = mapped_column(default=None)

    notes: Mapped[str | None] = mapped_column(default=None)

    @property
    def has_snmp_community(self) -> bool:
        """Masks snmp_community_encrypted for MfpDeviceOut — mirrors
        Printer.has_snmp_community exactly."""
        return bool(self.snmp_community_encrypted)

    @property
    def has_admin_password(self) -> bool:
        """Masks admin_password_encrypted for MfpDeviceOut — same masking
        pattern as has_snmp_community above."""
        return bool(self.admin_password_encrypted)
