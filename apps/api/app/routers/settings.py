from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.db import get_db
from app.deps import get_current_user
from app.integrations.classguard import ClassGuardClient, ClassGuardError
from app.integrations.mosyle import MosyleClient, MosyleError, run_sync
from app.models.classguard import ClassGuardSettings
from app.models.mosyle import MosyleSettings
from app.schemas.classguard import ClassGuardSettingsOut, ClassGuardSettingsUpdate, ClassGuardTestRequest, ClassGuardTestResult
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


@router.put("/mosyle", response_model=MosyleSettingsOut)
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


@router.post("/mosyle/test", response_model=MosyleTestResult)
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


@router.post("/mosyle/sync", response_model=MosyleSettingsOut)
async def sync_mosyle_devices(db: AsyncSession = Depends(get_db)):
    settings = await _get_or_create_settings(db)
    try:
        await run_sync(db)
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


@router.put("/classguard", response_model=ClassGuardSettingsOut)
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


@router.post("/classguard/test", response_model=ClassGuardTestResult)
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
