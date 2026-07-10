from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.copiers.connector import CapabilityNotSupported, ConnectionTestResult, refresh_device_meter
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.core.crypto import encrypt
from app.db import get_db
from app.deps import require_role
from app.models.copier_usage import CopierUsageRecord
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.schemas.copier_usage import CopierUsageRecordOut
from app.schemas.mfp_device import (
    ConnectorTypeOut,
    MfpDeviceCreate,
    MfpDeviceOut,
    MfpDeviceUpdate,
    available_connector_types,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


def _device_out(device: MfpDevice) -> MfpDeviceOut:
    """Built explicitly (not MfpDeviceOut.model_validate(device) via
    from_attributes) because "capabilities" is a synthetic grouping of 14
    flat cap_* columns on the model, not a single attribute Pydantic could
    pick up automatically."""
    return MfpDeviceOut(
        id=device.id,
        printer_id=device.printer_id,
        name=device.name,
        vendor=device.vendor,
        model=device.model,
        serial_number=device.serial_number,
        ip_address=device.ip_address,
        hostname=device.hostname,
        building=device.building,
        room=device.room,
        department=device.department,
        connector_type=device.connector_type,
        connector_config=device.connector_config,
        capabilities={
            "walkup_copy_accounting": device.cap_walkup_copy_accounting,
            "user_code_pin_auth": device.cap_user_code_pin_auth,
            "badge_card_auth": device.cap_badge_card_auth,
            "department_id_accounting": device.cap_department_id_accounting,
            "ldap_auth": device.cap_ldap_auth,
            "local_user_table": device.cap_local_user_table,
            "remote_user_provisioning": device.cap_remote_user_provisioning,
            "csv_accounting_export": device.cap_csv_accounting_export,
            "api_accounting_retrieval": device.cap_api_accounting_retrieval,
            "snmp_meter_counters": device.cap_snmp_meter_counters,
            "scan_accounting": device.cap_scan_accounting,
            "color_mono_accounting": device.cap_color_mono_accounting,
            "quotas": device.cap_quotas,
            "secure_print_release": device.cap_secure_print_release,
        },
        capabilities_source=device.capabilities_source,
        capabilities_detected_at=device.capabilities_detected_at,
        snmp_enabled=device.snmp_enabled,
        snmp_port=device.snmp_port,
        snmp_version=device.snmp_version,
        has_snmp_community=device.has_snmp_community,
        snmp_vendor_profile=device.snmp_vendor_profile,
        page_count_total=device.page_count_total,
        page_count_copy=device.page_count_copy,
        page_count_print=device.page_count_print,
        page_count_confidence=device.page_count_confidence,
        page_count_vendor_profile_used=device.page_count_vendor_profile_used,
        page_count_checked_at=device.page_count_checked_at,
        page_count_error=device.page_count_error,
        last_test_connection_at=device.last_test_connection_at,
        last_test_connection_ok=device.last_test_connection_ok,
        last_test_connection_message=device.last_test_connection_message,
        notes=device.notes,
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


async def _get_device_or_404(device_id: UUID, db: AsyncSession) -> MfpDevice:
    device = await db.get(MfpDevice, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MFP device not found")
    return device


_CAPABILITY_FIELD_MAP = {
    "walkup_copy_accounting": "cap_walkup_copy_accounting",
    "user_code_pin_auth": "cap_user_code_pin_auth",
    "badge_card_auth": "cap_badge_card_auth",
    "department_id_accounting": "cap_department_id_accounting",
    "ldap_auth": "cap_ldap_auth",
    "local_user_table": "cap_local_user_table",
    "remote_user_provisioning": "cap_remote_user_provisioning",
    "csv_accounting_export": "cap_csv_accounting_export",
    "api_accounting_retrieval": "cap_api_accounting_retrieval",
    "snmp_meter_counters": "cap_snmp_meter_counters",
    "scan_accounting": "cap_scan_accounting",
    "color_mono_accounting": "cap_color_mono_accounting",
    "quotas": "cap_quotas",
    "secure_print_release": "cap_secure_print_release",
}


@router.get("/connector-types", response_model=list[ConnectorTypeOut])
async def list_connector_types():
    """What the frontend's connector-type picker offers — only what's
    actually registered (app/copiers/registry.py), never an unimplemented
    vendor connector presented as a fake option."""
    return available_connector_types()


@router.post("", response_model=MfpDeviceOut, status_code=status.HTTP_201_CREATED)
async def create_mfp_device(payload: MfpDeviceCreate, db: AsyncSession = Depends(get_db)):
    if payload.connector_type not in CONNECTOR_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown connector_type: {payload.connector_type!r}",
        )
    data = payload.model_dump(exclude={"snmp_community"})
    device = MfpDevice(
        **data,
        snmp_community_encrypted=encrypt(payload.snmp_community)
        if payload.snmp_community
        else None,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return _device_out(device)


@router.get("", response_model=list[MfpDeviceOut])
async def list_mfp_devices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MfpDevice).order_by(MfpDevice.name))
    return [_device_out(d) for d in result.scalars().all()]


@router.get("/{device_id}", response_model=MfpDeviceOut)
async def get_mfp_device(device_id: UUID, db: AsyncSession = Depends(get_db)):
    device = await _get_device_or_404(device_id, db)
    return _device_out(device)


@router.patch("/{device_id}", response_model=MfpDeviceOut)
async def update_mfp_device(
    device_id: UUID, payload: MfpDeviceUpdate, db: AsyncSession = Depends(get_db)
):
    device = await _get_device_or_404(device_id, db)
    updates = payload.model_dump(exclude_unset=True, exclude={"snmp_community", "capabilities"})
    if "connector_type" in updates:
        if updates["connector_type"] not in CONNECTOR_REGISTRY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown connector_type: {updates['connector_type']!r}",
            )
    for field in ("snmp_version", "snmp_vendor_profile"):
        if field in updates:
            updates[field] = updates[field] or None
    for field, value in updates.items():
        setattr(device, field, value)

    if payload.snmp_community is not None:
        device.snmp_community_encrypted = (
            encrypt(payload.snmp_community) if payload.snmp_community else None
        )

    if payload.capabilities is not None:
        for schema_field, model_field in _CAPABILITY_FIELD_MAP.items():
            value = getattr(payload.capabilities, schema_field)
            if value is not None:
                setattr(device, model_field, value)
        device.capabilities_source = "manual"
        device.capabilities_detected_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(device)
    return _device_out(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mfp_device(device_id: UUID, db: AsyncSession = Depends(get_db)):
    device = await _get_device_or_404(device_id, db)
    await db.delete(device)
    await db.commit()


@router.post("/{device_id}/test-connection", response_model=MfpDeviceOut)
async def test_mfp_device_connection(device_id: UUID, db: AsyncSession = Depends(get_db)):
    device = await _get_device_or_404(device_id, db)
    connector = get_connector(device.connector_type)
    now = datetime.now(UTC)
    try:
        result: ConnectionTestResult = await connector.test_connection(device)
    except CapabilityNotSupported as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    device.last_test_connection_at = now
    device.last_test_connection_ok = result.ok
    device.last_test_connection_message = result.message
    await db.commit()
    await db.refresh(device)
    return _device_out(device)


@router.post("/{device_id}/check-capabilities", response_model=MfpDeviceOut)
async def check_mfp_device_capabilities(device_id: UUID, db: AsyncSession = Depends(get_db)):
    device = await _get_device_or_404(device_id, db)
    connector = get_connector(device.connector_type)
    try:
        report = await connector.get_capabilities(device)
    except CapabilityNotSupported as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    for schema_field, value in report.capabilities.items():
        model_field = _CAPABILITY_FIELD_MAP.get(schema_field)
        if model_field:
            setattr(device, model_field, value)
    device.capabilities_source = "connector_reported"
    device.capabilities_detected_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(device)
    return _device_out(device)


@router.post("/{device_id}/check-meter", response_model=MfpDeviceOut)
async def check_mfp_device_meter(device_id: UUID, db: AsyncSession = Depends(get_db)):
    """Dispatches to whichever connector this device actually uses — 400s
    only if that connector doesn't implement meter reads at all (e.g.
    generic_csv). When this device is linked to an existing CUPS-proxied
    Printer (printer_id set), reads that printer's already-polled
    page_count_* fields directly instead of re-polling the same SNMP OIDs
    a second time — the printer is already covered by app/main.py's
    30-min counter poll loop."""
    device = await _get_device_or_404(device_id, db)

    if device.printer_id is not None:
        printer = await db.get(Printer, device.printer_id)
        if printer is not None:
            device.page_count_total = printer.page_count_total
            device.page_count_copy = printer.page_count_copy
            device.page_count_print = printer.page_count_print
            device.page_count_confidence = printer.page_count_confidence
            device.page_count_vendor_profile_used = printer.page_count_vendor_profile_used
            device.page_count_checked_at = printer.page_count_checked_at
            device.page_count_error = printer.page_count_error
            await db.commit()
            await db.refresh(device)
            return _device_out(device)

    connector = get_connector(device.connector_type)
    try:
        await refresh_device_meter(device, connector)
    except CapabilityNotSupported as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(device)
    return _device_out(device)


@router.get("/{device_id}/usage", response_model=list[CopierUsageRecordOut])
async def list_mfp_device_usage(
    device_id: UUID, limit: int = 100, db: AsyncSession = Depends(get_db)
):
    """Recent CopierUsageRecord rows for this device, newest first — a
    simple paginated feed, not aggregated (see app/reports/aggregation.py
    for the combined-reporting rollups)."""
    await _get_device_or_404(device_id, db)
    result = await db.execute(
        select(CopierUsageRecord)
        .where(CopierUsageRecord.mfp_device_id == device_id)
        .order_by(CopierUsageRecord.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
