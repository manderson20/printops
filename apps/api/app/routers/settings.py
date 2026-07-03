from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.db import get_db
from app.deps import get_current_user, require_role
from app.integrations.classguard import ClassGuardClient, ClassGuardError
from app.integrations.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceError
from app.integrations.google_workspace import run_sync as run_google_workspace_sync
from app.integrations.mosyle import MosyleClient, MosyleError
from app.integrations.mosyle import run_sync as run_mosyle_sync
from app.models.classguard import ClassGuardSettings
from app.models.google_sso import GoogleSsoSettings
from app.models.google_workspace import GoogleWorkspaceSettings, GoogleWorkspaceUser
from app.models.mosyle import MosyleSettings
from app.schemas.classguard import ClassGuardSettingsOut, ClassGuardSettingsUpdate, ClassGuardTestRequest, ClassGuardTestResult
from app.schemas.google_sso import GoogleSsoSettingsOut, GoogleSsoSettingsUpdate
from app.schemas.google_workspace import (
    GoogleWorkspaceSettingsOut,
    GoogleWorkspaceSettingsUpdate,
    GoogleWorkspaceTestResult,
    GoogleWorkspaceUserOut,
)
from app.schemas.mosyle import MosyleSettingsOut, MosyleSettingsUpdate, MosyleTestResult

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


@router.put("/mosyle", response_model=MosyleSettingsOut, dependencies=[Depends(require_role("admin"))])
async def update_mosyle_settings(
    payload: MosyleSettingsUpdate, db: AsyncSession = Depends(get_db)
):
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


@router.post("/mosyle/test", response_model=MosyleTestResult, dependencies=[Depends(require_role("admin"))])
async def test_mosyle_connection(
    payload: MosyleSettingsUpdate, db: AsyncSession = Depends(get_db)
):
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
        return MosyleTestResult(ok=False, error="Base URL, access token, admin email, and admin password are all required.")

    client = MosyleClient(
        base_url=base_url, access_token=access_token, admin_email=admin_email, admin_password=admin_password
    )
    try:
        devices = await client.list_devices()
    except MosyleError as exc:
        return MosyleTestResult(ok=False, error=str(exc))
    return MosyleTestResult(ok=True, device_count=len(devices))


@router.post("/mosyle/sync", response_model=MosyleSettingsOut, dependencies=[Depends(require_role("admin"))])
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


@router.put("/classguard", response_model=ClassGuardSettingsOut, dependencies=[Depends(require_role("admin"))])
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
    "/classguard/test", response_model=ClassGuardTestResult, dependencies=[Depends(require_role("admin"))]
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
    return ClassGuardTestResult(ok=True, error=f"Connected, but no active lease found for {payload.test_ip}.")


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
    )


@router.get("/google-workspace", response_model=GoogleWorkspaceSettingsOut)
async def get_google_workspace_settings(db: AsyncSession = Depends(get_db)):
    return _google_workspace_to_out(await _get_or_create_google_workspace_settings(db))


@router.put(
    "/google-workspace", response_model=GoogleWorkspaceSettingsOut, dependencies=[Depends(require_role("admin"))]
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
        decrypt(settings.service_account_json_encrypted) if settings.service_account_json_encrypted else None
    )
    admin_email = payload.admin_email or settings.admin_email
    customer_id = payload.customer_id or settings.customer_id

    if not (service_account_json and admin_email):
        return GoogleWorkspaceTestResult(
            ok=False, error="Service account JSON and admin email are both required."
        )

    try:
        client = GoogleWorkspaceClient(
            service_account_json=service_account_json, admin_email=admin_email, customer_id=customer_id
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
    return [GoogleWorkspaceUserOut(email=u.email, name=u.name) for u in result.scalars().all()]


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
            "value. The secret looks different (e.g. starts with \"GOCSPX-\"), not like "
            "\"...apps.googleusercontent.com\".",
        )
    if secret.endswith(".apps.googleusercontent.com"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That looks like a Client ID, not a Client Secret — Client IDs end in "
            '".apps.googleusercontent.com"; the secret is a shorter, differently-formatted '
            'string (e.g. starting with "GOCSPX-").',
        )


@router.put("/google-sso", response_model=GoogleSsoSettingsOut, dependencies=[Depends(require_role("admin"))])
async def update_google_sso_settings(payload: GoogleSsoSettingsUpdate, db: AsyncSession = Depends(get_db)):
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
