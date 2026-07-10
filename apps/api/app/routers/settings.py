import csv
import io
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.db import get_db
from app.deps import get_current_user, require_role
from app.integrations.classguard import ClassGuardClient, ClassGuardError
from app.integrations.git_update import REPO_ROOT
from app.integrations.google_workspace import (
    GoogleWorkspaceClient,
    GoogleWorkspaceError,
    org_unit_matches,
)
from app.integrations.google_workspace import run_sync as run_google_workspace_sync
from app.integrations.mosyle import MosyleClient, MosyleError
from app.integrations.mosyle import run_sync as run_mosyle_sync
from app.ldap_relay.service import get_or_create_ldap_relay_settings
from app.models.classguard import ClassGuardSettings
from app.models.google_sso import GoogleSsoSettings
from app.models.google_workspace import GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.mosyle import MosyleSettings
from app.models.release import PrintReleaseSettings
from app.models.report import ReportFormulaSettings
from app.models.snmp import SnmpDefaultsSettings
from app.models.zabbix import ZabbixSettings
from app.printers.snmp_counters import get_or_create_snmp_defaults
from app.quotas.service import get_or_create_quota_settings
from app.reports.untracked_copies import get_or_create_untracked_copy_settings
from app.schemas.classguard import (
    ClassGuardSettingsOut,
    ClassGuardSettingsUpdate,
    ClassGuardTestRequest,
    ClassGuardTestResult,
)
from app.schemas.google_sso import GoogleSsoSettingsOut, GoogleSsoSettingsUpdate
from app.schemas.google_workspace import (
    GoogleWorkspaceSettingsOut,
    GoogleWorkspaceSettingsUpdate,
    GoogleWorkspaceTestResult,
    GoogleWorkspaceUserOut,
)
from app.schemas.ldap_relay import LdapRelaySettingsOut, LdapRelaySettingsUpdate
from app.schemas.mosyle import MosyleSettingsOut, MosyleSettingsUpdate, MosyleTestResult
from app.schemas.quota import QuotaSettingsOut, QuotaSettingsUpdate
from app.schemas.release import PrintReleaseSettingsOut, PrintReleaseSettingsUpdate
from app.schemas.report import ReportFormulaSettingsOut, ReportFormulaSettingsUpdate
from app.schemas.session import SessionSettingsOut, SessionSettingsUpdate
from app.schemas.snmp import SnmpDefaultsOut, SnmpDefaultsUpdate
from app.schemas.syslog import SyslogSettingsOut, SyslogSettingsUpdate
from app.schemas.untracked_copies import UntrackedCopySettingsOut, UntrackedCopySettingsUpdate
from app.schemas.zabbix import ZabbixSettingsOut, ZabbixSettingsUpdate
from app.sessions.service import get_or_create_session_settings
from app.syslog.service import get_or_create_syslog_settings

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _get_or_create_settings(db: AsyncSession) -> MosyleSettings:
    result = await db.execute(select(MosyleSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = MosyleSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _to_out(settings: MosyleSettings) -> MosyleSettingsOut:
    return MosyleSettingsOut(
        base_url=settings.base_url,
        admin_email=settings.admin_email,
        has_access_token=bool(settings.access_token_encrypted),
        has_admin_password=bool(settings.admin_password_encrypted),
        enabled=settings.enabled,
        last_synced_at=settings.last_synced_at,
        last_sync_error=settings.last_sync_error,
        device_count=settings.device_count,
    )


@router.get("/mosyle", response_model=MosyleSettingsOut)
async def get_mosyle_settings(db: AsyncSession = Depends(get_db)):
    return _to_out(await _get_or_create_settings(db))


@router.put(
    "/mosyle", response_model=MosyleSettingsOut, dependencies=[Depends(require_role("admin"))]
)
async def update_mosyle_settings(payload: MosyleSettingsUpdate, db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if "base_url" in updates and updates["base_url"] is not None:
        settings.base_url = updates["base_url"]
    if "admin_email" in updates and updates["admin_email"] is not None:
        settings.admin_email = updates["admin_email"]
    if "enabled" in updates and updates["enabled"] is not None:
        settings.enabled = updates["enabled"]
    # Secrets: only overwrite when a non-empty value is actually provided —
    # leaves the previously-saved credential in place on an edit where the
    # admin didn't retype it (we never send decrypted secrets back to the
    # frontend to pre-fill, so "untouched" means "omitted/blank" here).
    if updates.get("access_token"):
        settings.access_token_encrypted = encrypt(updates["access_token"])
    if updates.get("admin_password"):
        settings.admin_password_encrypted = encrypt(updates["admin_password"])

    await db.commit()
    await db.refresh(settings)
    return _to_out(settings)


@router.post(
    "/mosyle/test", response_model=MosyleTestResult, dependencies=[Depends(require_role("admin"))]
)
async def test_mosyle_connection(payload: MosyleSettingsUpdate, db: AsyncSession = Depends(get_db)):
    """Tests connectivity using the provided values, falling back to
    already-saved ones for anything omitted — lets an admin test with a
    newly-entered token without having to re-enter fields they didn't
    change."""
    settings = await _get_or_create_settings(db)
    base_url = payload.base_url or settings.base_url
    admin_email = payload.admin_email or settings.admin_email
    access_token = payload.access_token or (
        decrypt(settings.access_token_encrypted) if settings.access_token_encrypted else None
    )
    admin_password = payload.admin_password or (
        decrypt(settings.admin_password_encrypted) if settings.admin_password_encrypted else None
    )

    if not (base_url and admin_email and access_token and admin_password):
        return MosyleTestResult(
            ok=False,
            error="Base URL, access token, admin email, and admin password are all required.",
        )

    client = MosyleClient(
        base_url=base_url,
        access_token=access_token,
        admin_email=admin_email,
        admin_password=admin_password,
    )
    try:
        devices = await client.list_devices()
    except MosyleError as exc:
        return MosyleTestResult(ok=False, error=str(exc))
    return MosyleTestResult(ok=True, device_count=len(devices))


@router.post(
    "/mosyle/sync", response_model=MosyleSettingsOut, dependencies=[Depends(require_role("admin"))]
)
async def sync_mosyle_devices(db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_settings(db)
    try:
        await run_mosyle_sync(db)
    except MosyleError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await db.refresh(settings)
    return _to_out(settings)


async def _get_or_create_classguard_settings(db: AsyncSession) -> ClassGuardSettings:
    result = await db.execute(select(ClassGuardSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ClassGuardSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _classguard_to_out(settings: ClassGuardSettings) -> ClassGuardSettingsOut:
    return ClassGuardSettingsOut(
        base_url=settings.base_url,
        has_access_token=bool(settings.access_token_encrypted),
        enabled=settings.enabled,
        last_test_at=settings.last_test_at,
        last_test_error=settings.last_test_error,
    )


@router.get("/classguard", response_model=ClassGuardSettingsOut)
async def get_classguard_settings(db: AsyncSession = Depends(get_db)):
    return _classguard_to_out(await _get_or_create_classguard_settings(db))


@router.put(
    "/classguard",
    response_model=ClassGuardSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_classguard_settings(
    payload: ClassGuardSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _get_or_create_classguard_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("base_url") is not None:
        settings.base_url = updates["base_url"]
    if updates.get("enabled") is not None:
        settings.enabled = updates["enabled"]
    if updates.get("access_token"):
        settings.access_token_encrypted = encrypt(updates["access_token"])

    await db.commit()
    await db.refresh(settings)
    return _classguard_to_out(settings)


@router.post(
    "/classguard/test",
    response_model=ClassGuardTestResult,
    dependencies=[Depends(require_role("admin"))],
)
async def test_classguard_connection(
    payload: ClassGuardTestRequest, db: AsyncSession = Depends(get_db)
):
    """Tests connectivity by looking up a real IP — ClassGuard has no
    separate health endpoint, so this exercises the actual lookup path.
    A 404 (no active lease for that IP) still counts as ok=True since it
    proves auth + reachability; only unreachable/auth-failure counts as a
    real failure."""
    settings = await _get_or_create_classguard_settings(db)
    base_url = payload.base_url or settings.base_url
    access_token = payload.access_token or (
        decrypt(settings.access_token_encrypted) if settings.access_token_encrypted else None
    )

    if not (base_url and access_token):
        return ClassGuardTestResult(ok=False, error="Base URL and access token are both required.")

    client = ClassGuardClient(base_url=base_url, access_token=access_token)
    now = datetime.now(UTC)
    try:
        mac = await client.lookup_mac(payload.test_ip)
    except ClassGuardError as exc:
        settings.last_test_at = now
        settings.last_test_error = str(exc)
        await db.commit()
        return ClassGuardTestResult(ok=False, error=str(exc))

    settings.last_test_at = now
    settings.last_test_error = None
    await db.commit()
    if mac:
        return ClassGuardTestResult(ok=True, mac_address=mac)
    return ClassGuardTestResult(
        ok=True, error=f"Connected, but no active lease found for {payload.test_ip}."
    )


def _snmp_defaults_to_out(settings: SnmpDefaultsSettings) -> SnmpDefaultsOut:
    return SnmpDefaultsOut(
        version=settings.version,
        port=settings.port,
        has_community=bool(settings.community_encrypted),
        enabled=settings.enabled,
        retention_days=settings.retention_days,
    )


@router.get("/snmp", response_model=SnmpDefaultsOut)
async def get_snmp_defaults(db: AsyncSession = Depends(get_db)):
    return _snmp_defaults_to_out(await get_or_create_snmp_defaults(db))


@router.put("/snmp", response_model=SnmpDefaultsOut, dependencies=[Depends(require_role("admin"))])
async def update_snmp_defaults(payload: SnmpDefaultsUpdate, db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_snmp_defaults(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("version") is not None:
        settings.version = updates["version"]
    if updates.get("port") is not None:
        settings.port = updates["port"]
    if updates.get("enabled") is not None:
        settings.enabled = updates["enabled"]
    if updates.get("retention_days") is not None:
        settings.retention_days = updates["retention_days"]
    if updates.get("community"):
        settings.community_encrypted = encrypt(updates["community"])

    await db.commit()
    await db.refresh(settings)
    return _snmp_defaults_to_out(settings)


@router.get("/syslog", response_model=SyslogSettingsOut)
async def get_syslog_settings(db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_syslog_settings(db)
    return SyslogSettingsOut(
        enabled=settings.enabled,
        port=settings.port,
        min_severity=settings.min_severity,
        retention_days=settings.retention_days,
    )


@router.put(
    "/syslog", response_model=SyslogSettingsOut, dependencies=[Depends(require_role("admin"))]
)
async def update_syslog_settings(payload: SyslogSettingsUpdate, db: AsyncSession = Depends(get_db)):
    """Org-wide kill switch + noise floor for the syslog collector
    (infra/syslog-relay/) — off by default, matching SNMP defaults/LDAP
    relay. `port` here is informational only for now: the relay actually
    binds infra/syslog-relay/printops-syslog-relay.service's configured
    port at process start, so changing it here doesn't move the listener
    without also updating and restarting that service."""
    settings = await get_or_create_syslog_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("enabled") is not None:
        settings.enabled = updates["enabled"]
    if updates.get("port") is not None:
        settings.port = updates["port"]
    if updates.get("min_severity") is not None:
        settings.min_severity = updates["min_severity"]
    if updates.get("retention_days") is not None:
        settings.retention_days = updates["retention_days"]

    await db.commit()
    await db.refresh(settings)
    return SyslogSettingsOut(
        enabled=settings.enabled,
        port=settings.port,
        min_severity=settings.min_severity,
        retention_days=settings.retention_days,
    )


async def _get_or_create_google_workspace_settings(db: AsyncSession) -> GoogleWorkspaceSettings:
    result = await db.execute(select(GoogleWorkspaceSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = GoogleWorkspaceSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _google_workspace_to_out(settings: GoogleWorkspaceSettings) -> GoogleWorkspaceSettingsOut:
    return GoogleWorkspaceSettingsOut(
        admin_email=settings.admin_email,
        customer_id=settings.customer_id,
        has_service_account_json=bool(settings.service_account_json_encrypted),
        enabled=settings.enabled,
        last_synced_at=settings.last_synced_at,
        last_sync_error=settings.last_sync_error,
        device_count=settings.device_count,
        staff_org_unit_path=settings.staff_org_unit_path,
        auto_create_copier_identity_from_employee_id=settings.auto_create_copier_identity_from_employee_id,
        auto_copier_identity_type=settings.auto_copier_identity_type,
    )


@router.get("/google-workspace", response_model=GoogleWorkspaceSettingsOut)
async def get_google_workspace_settings(db: AsyncSession = Depends(get_db)):
    return _google_workspace_to_out(await _get_or_create_google_workspace_settings(db))


@router.put(
    "/google-workspace",
    response_model=GoogleWorkspaceSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_google_workspace_settings(
    payload: GoogleWorkspaceSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _get_or_create_google_workspace_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("admin_email") is not None:
        settings.admin_email = updates["admin_email"]
    if updates.get("customer_id") is not None:
        settings.customer_id = updates["customer_id"]
    if updates.get("enabled") is not None:
        settings.enabled = updates["enabled"]
    if updates.get("service_account_json"):
        settings.service_account_json_encrypted = encrypt(updates["service_account_json"])
    if "staff_org_unit_path" in updates:
        # Blank clears the filter (falls back to "include everyone with an
        # Employee ID") rather than being rejected as invalid input.
        settings.staff_org_unit_path = updates["staff_org_unit_path"] or None
    if updates.get("auto_create_copier_identity_from_employee_id") is not None:
        settings.auto_create_copier_identity_from_employee_id = updates[
            "auto_create_copier_identity_from_employee_id"
        ]
    if updates.get("auto_copier_identity_type") is not None:
        settings.auto_copier_identity_type = updates["auto_copier_identity_type"]

    await db.commit()
    await db.refresh(settings)
    return _google_workspace_to_out(settings)


@router.post(
    "/google-workspace/test",
    response_model=GoogleWorkspaceTestResult,
    dependencies=[Depends(require_role("admin"))],
)
async def test_google_workspace_connection(
    payload: GoogleWorkspaceSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    """Tests connectivity using the provided values, falling back to
    already-saved ones for anything omitted."""
    settings = await _get_or_create_google_workspace_settings(db)
    service_account_json = payload.service_account_json or (
        decrypt(settings.service_account_json_encrypted)
        if settings.service_account_json_encrypted
        else None
    )
    admin_email = payload.admin_email or settings.admin_email
    customer_id = payload.customer_id or settings.customer_id

    if not (service_account_json and admin_email):
        return GoogleWorkspaceTestResult(
            ok=False, error="Service account JSON and admin email are both required."
        )

    try:
        client = GoogleWorkspaceClient(
            service_account_json=service_account_json,
            admin_email=admin_email,
            customer_id=customer_id,
        )
        devices = await client.list_chromeos_devices()
    except GoogleWorkspaceError as exc:
        return GoogleWorkspaceTestResult(ok=False, error=str(exc))
    return GoogleWorkspaceTestResult(ok=True, device_count=len(devices))


@router.post(
    "/google-workspace/sync",
    response_model=GoogleWorkspaceSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def sync_google_workspace_devices(db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_google_workspace_settings(db)
    try:
        await run_google_workspace_sync(db)
    except GoogleWorkspaceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await db.refresh(settings)
    return _google_workspace_to_out(settings)


@router.get(
    "/google-workspace/users",
    response_model=list[GoogleWorkspaceUserOut],
    dependencies=[Depends(require_role("admin"))],
)
async def list_google_workspace_users(db: AsyncSession = Depends(get_db)):
    """The synced canonical email roster (app/integrations/google_workspace.py's
    sync_users) — used by the device-override admin UI to validate/autocomplete
    a correction email against a real org address rather than free text."""
    result = await db.execute(select(GoogleWorkspaceUser).order_by(GoogleWorkspaceUser.email))
    return [
        GoogleWorkspaceUserOut(
            email=u.email, name=u.name, employee_id=u.employee_id, aliases=u.aliases
        )
        for u in result.scalars().all()
    ]


@router.get(
    "/google-workspace/org-units",
    response_model=list[str],
    dependencies=[Depends(require_role("admin"))],
)
async def list_google_workspace_org_units(db: AsyncSession = Depends(get_db)):
    """Distinct org_unit_path values from the synced roster (sync_users) —
    powers the OU picker on Settings > Permissions (app/models/user.py's
    granted_ou_paths), so an admin picks from real, currently-populated org
    units instead of typing a path blind with no idea what actually exists
    in the directory.

    Filtered to GoogleWorkspaceSettings.staff_org_unit_path (and anything
    nested under it) when configured — same reasoning as
    export_copier_pin_roster below: unscoped, this directory's OU list is
    dominated by non-staff structure (student grade levels, Classroom
    Devices, Apple-VPP, IT test OUs, ...) that has nothing to do with who
    an OU Viewer account should see report data for. Falls back to
    everything only when that setting isn't configured, so the picker is
    never empty."""
    settings = await _get_or_create_google_workspace_settings(db)
    result = await db.execute(
        select(GoogleWorkspaceUser.org_unit_path).where(
            GoogleWorkspaceUser.org_unit_path.is_not(None)
        )
    )
    paths = {path for (path,) in result.all() if path}
    if settings.staff_org_unit_path:
        paths = {path for path in paths if org_unit_matches(path, settings.staff_org_unit_path)}
    return sorted(paths)


@router.get(
    "/google-workspace/copier-pin-roster.csv",
    dependencies=[Depends(require_role("admin"))],
)
async def export_copier_pin_roster(db: AsyncSession = Depends(get_db)):
    """A starting point for loading staff into a copier's local PIN list
    (e.g. Konica Minolta's Account Track/User Authentication user
    registration) — PIN is each person's Google Workspace Employee ID, so
    entering that number at the copier identifies them the same way it
    would in PrintOps. Skips anyone without an Employee ID set in their
    Workspace profile rather than fabricating one.

    This is a best-effort default column layout (Name, Email, PIN), not a
    confirmed match for any specific bizhub firmware's bulk-import
    template — the local "User Registration" import format varies by
    device/firmware, so check it against a real device's admin panel and
    adjust the columns here if needed.

    Also filtered to GoogleWorkspaceSettings.staff_org_unit_path (and
    anything nested under it) when configured — without it, this roster
    would include anyone with an Employee ID set, which in practice can
    include students, not just staff (every org's OU naming is different,
    so this is never guessed/hardcoded)."""
    settings = await _get_or_create_google_workspace_settings(db)
    result = await db.execute(
        select(GoogleWorkspaceUser)
        .where(GoogleWorkspaceUser.employee_id.is_not(None))
        .order_by(GoogleWorkspaceUser.email)
    )
    users = result.scalars().all()
    if settings.staff_org_unit_path:
        users = [
            u for u in users if org_unit_matches(u.org_unit_path, settings.staff_org_unit_path)
        ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Name", "Email", "PIN"])
    for user in users:
        writer.writerow([user.name or "", user.email, user.employee_id])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=copier-pin-roster.csv"},
    )


async def _get_or_create_google_sso_settings(db: AsyncSession) -> GoogleSsoSettings:
    result = await db.execute(select(GoogleSsoSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = GoogleSsoSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _parse_admin_emails(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [email.strip() for email in raw.split(",") if email.strip()]


def _normalize_redirect_base_url(value: str) -> str:
    """Strips a trailing slash and, if someone pastes in the full
    callback URL instead of just the origin, the /auth/google/callback
    suffix too — app/routers/auth.py appends that itself when building
    the redirect_uri, so a stored value that already includes it would
    silently double it up."""
    trimmed = value.strip().rstrip("/")
    suffix = "/auth/google/callback"
    if trimmed.endswith(suffix):
        trimmed = trimmed[: -len(suffix)]
    return trimmed


def _google_sso_to_out(settings: GoogleSsoSettings) -> GoogleSsoSettingsOut:
    return GoogleSsoSettingsOut(
        client_id=settings.client_id,
        has_client_secret=bool(settings.client_secret_encrypted),
        workspace_domain=settings.workspace_domain,
        initial_admin_emails=_parse_admin_emails(settings.initial_admin_emails),
        redirect_base_url=settings.redirect_base_url,
        enabled=settings.enabled,
    )


@router.get("/google-sso", response_model=GoogleSsoSettingsOut)
async def get_google_sso_settings(db: AsyncSession = Depends(get_db)):
    return _google_sso_to_out(await _get_or_create_google_sso_settings(db))


def _validate_client_secret(secret: str, client_id: str | None) -> None:
    """Google Client IDs and Client Secrets are easy to mix up when
    copy-pasting from Google Cloud Console's credentials page — both are
    just long opaque strings sitting next to each other. Catch the most
    common version of that mistake (pasting the Client ID into the
    Secret field) before it's stored, rather than letting it surface
    later as a confusing "invalid_client" error from Google mid-login."""
    if client_id and secret == client_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Client Secret is identical to Client ID — you've likely pasted the wrong "
            'value. The secret looks different (e.g. starts with "GOCSPX-"), not like '
            '"...apps.googleusercontent.com".',
        )
    if secret.endswith(".apps.googleusercontent.com"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That looks like a Client ID, not a Client Secret — Client IDs end in "
            '".apps.googleusercontent.com"; the secret is a shorter, differently-formatted '
            'string (e.g. starting with "GOCSPX-").',
        )


@router.put(
    "/google-sso",
    response_model=GoogleSsoSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_google_sso_settings(
    payload: GoogleSsoSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _get_or_create_google_sso_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("client_secret"):
        _validate_client_secret(
            updates["client_secret"], updates.get("client_id") or settings.client_id
        )
    if updates.get("client_id") is not None:
        settings.client_id = updates["client_id"]
    if updates.get("workspace_domain") is not None:
        settings.workspace_domain = updates["workspace_domain"]
    if updates.get("redirect_base_url") is not None:
        settings.redirect_base_url = _normalize_redirect_base_url(updates["redirect_base_url"])
    if updates.get("initial_admin_emails") is not None:
        settings.initial_admin_emails = ",".join(updates["initial_admin_emails"])
    if updates.get("enabled") is not None:
        settings.enabled = updates["enabled"]
    if updates.get("client_secret"):
        settings.client_secret_encrypted = encrypt(updates["client_secret"])

    await db.commit()
    await db.refresh(settings)
    return _google_sso_to_out(settings)


async def _get_or_create_zabbix_settings(db: AsyncSession) -> ZabbixSettings:
    result = await db.execute(select(ZabbixSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ZabbixSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _zabbix_to_out(settings: ZabbixSettings) -> ZabbixSettingsOut:
    return ZabbixSettingsOut(
        enabled=settings.enabled, api_token=settings.api_token, base_url=settings.base_url
    )


@router.get("/zabbix", response_model=ZabbixSettingsOut)
async def get_zabbix_settings(db: AsyncSession = Depends(get_db)):
    return _zabbix_to_out(await _get_or_create_zabbix_settings(db))


@router.put(
    "/zabbix", response_model=ZabbixSettingsOut, dependencies=[Depends(require_role("admin"))]
)
async def update_zabbix_settings(payload: ZabbixSettingsUpdate, db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_zabbix_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("base_url") is not None:
        settings.base_url = _normalize_redirect_base_url(updates["base_url"])
    if updates.get("enabled") is not None:
        # Generated lazily on first enable, not at row-creation time —
        # mirrors Printer.release_token (app/routers/printers.py), so a
        # never-enabled integration never has a live token sitting unused
        # in the DB.
        if updates["enabled"] and not settings.api_token:
            settings.api_token = secrets.token_urlsafe(32)
        settings.enabled = updates["enabled"]

    await db.commit()
    await db.refresh(settings)
    return _zabbix_to_out(settings)


@router.post(
    "/zabbix/regenerate-token",
    response_model=ZabbixSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def regenerate_zabbix_token(db: AsyncSession = Depends(get_db)):
    """Immediately invalidates the old token — app.deps.verify_zabbix_token
    looks it up live on every call, same as
    regenerate_release_token (app/routers/printers.py)."""
    settings = await _get_or_create_zabbix_settings(db)
    settings.api_token = secrets.token_urlsafe(32)
    await db.commit()
    await db.refresh(settings)
    return _zabbix_to_out(settings)


@router.get("/zabbix/template", dependencies=[Depends(require_role("admin"))])
async def download_zabbix_template():
    """A static, generic Zabbix template (no per-install values baked in —
    those are filled in as Zabbix host macros at import time, see the
    Settings > Integrations > Zabbix setup guide) — the same file works
    for any PrintOps install."""
    return FileResponse(
        REPO_ROOT / "infra" / "zabbix" / "printops_template.yaml",
        media_type="application/x-yaml",
        filename="printops_zabbix_template.yaml",
    )


async def _get_or_create_report_formula_settings(db: AsyncSession) -> ReportFormulaSettings:
    result = await db.execute(select(ReportFormulaSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ReportFormulaSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


FORMULA_FIELDS = (
    "cost_per_page_mono",
    "cost_per_page_color",
    "sheets_per_tree",
    "co2_grams_per_sheet",
    "cost_per_sheet_paper",
)


def _report_formula_settings_out(settings: ReportFormulaSettings) -> ReportFormulaSettingsOut:
    return ReportFormulaSettingsOut(**{field: getattr(settings, field) for field in FORMULA_FIELDS})


@router.get("/report-formulas", response_model=ReportFormulaSettingsOut)
async def get_report_formula_settings(db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_report_formula_settings(db)
    return _report_formula_settings_out(settings)


@router.put(
    "/report-formulas",
    response_model=ReportFormulaSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_report_formula_settings(
    payload: ReportFormulaSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _get_or_create_report_formula_settings(db)
    updates = payload.model_dump(exclude_unset=True)
    for field in FORMULA_FIELDS:
        if updates.get(field) is not None:
            setattr(settings, field, updates[field])

    await db.commit()
    await db.refresh(settings)
    return _report_formula_settings_out(settings)


@router.get("/untracked-copies", response_model=UntrackedCopySettingsOut)
async def get_untracked_copy_settings(db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_untracked_copy_settings(db)
    return UntrackedCopySettingsOut(enabled=settings.enabled, enabled_at=settings.enabled_at)


@router.put(
    "/untracked-copies",
    response_model=UntrackedCopySettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_untracked_copy_settings(
    payload: UntrackedCopySettingsUpdate, db: AsyncSession = Depends(get_db)
):
    """A False -> True transition re-stamps enabled_at to now — see
    UntrackedCopySettings' docstring (app/models/untracked_copies.py) for
    why this must never reach back before the moment it's actually on."""
    settings = await get_or_create_untracked_copy_settings(db)
    if payload.enabled is not None:
        if payload.enabled and not settings.enabled:
            settings.enabled_at = datetime.now(UTC)
        settings.enabled = payload.enabled

    await db.commit()
    await db.refresh(settings)
    return UntrackedCopySettingsOut(enabled=settings.enabled, enabled_at=settings.enabled_at)


async def _get_or_create_print_release_settings(db: AsyncSession) -> PrintReleaseSettings:
    result = await db.execute(select(PrintReleaseSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = PrintReleaseSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.get("/print-release", response_model=PrintReleaseSettingsOut)
async def get_print_release_settings(db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_print_release_settings(db)
    return PrintReleaseSettingsOut(hold_expiry_hours=settings.hold_expiry_hours)


@router.put(
    "/print-release",
    response_model=PrintReleaseSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_print_release_settings(
    payload: PrintReleaseSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    settings = await _get_or_create_print_release_settings(db)
    if payload.hold_expiry_hours is not None:
        settings.hold_expiry_hours = payload.hold_expiry_hours
    await db.commit()
    await db.refresh(settings)
    return PrintReleaseSettingsOut(hold_expiry_hours=settings.hold_expiry_hours)


@router.get("/quotas", response_model=QuotaSettingsOut)
async def get_quota_settings(db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_quota_settings(db)
    return QuotaSettingsOut(enabled=settings.enabled)


@router.put(
    "/quotas",
    response_model=QuotaSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_quota_settings(payload: QuotaSettingsUpdate, db: AsyncSession = Depends(get_db)):
    """Org-wide kill switch for page-quota enforcement — off by default, so
    configuring PrinterUserQuota rows on a printer never starts holding
    jobs until an admin explicitly opts in here (see
    app/quotas/service.py:resolve_hold_reason)."""
    settings = await get_or_create_quota_settings(db)
    if payload.enabled is not None:
        settings.enabled = payload.enabled
    await db.commit()
    await db.refresh(settings)
    return QuotaSettingsOut(enabled=settings.enabled)


@router.get("/session", response_model=SessionSettingsOut)
async def get_session_settings(db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_session_settings(db)
    return SessionSettingsOut(idle_timeout_minutes=settings.idle_timeout_minutes)


@router.put(
    "/session",
    response_model=SessionSettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_session_settings(
    payload: SessionSettingsUpdate, db: AsyncSession = Depends(get_db)
):
    """How long a session can sit idle before its token is allowed to
    expire — POST /auth/refresh (app/routers/auth.py) is what actually
    slides the window forward on activity; this just controls the
    duration it slides by. Takes effect on each user's very next refresh,
    not just future logins, since /auth/refresh reads this live."""
    settings = await get_or_create_session_settings(db)
    if payload.idle_timeout_minutes is not None:
        settings.idle_timeout_minutes = payload.idle_timeout_minutes
    await db.commit()
    await db.refresh(settings)
    return SessionSettingsOut(idle_timeout_minutes=settings.idle_timeout_minutes)


@router.get("/ldap", response_model=LdapRelaySettingsOut)
async def get_ldap_relay_settings(db: AsyncSession = Depends(get_db)):
    settings = await get_or_create_ldap_relay_settings(db)
    return LdapRelaySettingsOut(
        enabled=settings.enabled, base_dn=settings.base_dn, port=settings.port
    )


@router.put(
    "/ldap",
    response_model=LdapRelaySettingsOut,
    dependencies=[Depends(require_role("admin"))],
)
async def update_ldap_relay_settings(
    payload: LdapRelaySettingsUpdate, db: AsyncSession = Depends(get_db)
):
    """Org-wide kill switch + shared base DN for the LDAP address-book
    relay (infra/ldap-relay/) — off by default, so configuring per-printer
    bind credentials never actually serves anything until an admin opts in
    here (see app/routers/internal.py's ldap_bind/ldap_search)."""
    settings = await get_or_create_ldap_relay_settings(db)
    if payload.enabled is not None:
        settings.enabled = payload.enabled
    if payload.base_dn is not None:
        settings.base_dn = payload.base_dn
    if payload.port is not None:
        settings.port = payload.port
    await db.commit()
    await db.refresh(settings)
    return LdapRelaySettingsOut(
        enabled=settings.enabled, base_dn=settings.base_dn, port=settings.port
    )
