from unittest.mock import patch

import pytest

from app.copiers.canon_department_id import CanonDepartmentIdConnector
from app.copiers.connector import CapabilityNotSupported
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.models.mfp_device import MfpDevice

# Same live-captured fixtures as tests/test_snmp_counters.py — real
# snmpwalk -On output against this district's Canon MF642C/643C/644C.
CANON_LABELS_OUTPUT = """\
.1.3.6.1.4.1.1602.1.11.2.1.1.2.1 = STRING: "Total 1"
.1.3.6.1.4.1.1602.1.11.2.1.1.2.17 = STRING: "Copy (Total 1)"
.1.3.6.1.4.1.1602.1.11.2.1.1.2.25 = STRING: "Print (Total 1)"
"""
CANON_VALUES_OUTPUT = """\
.1.3.6.1.4.1.1602.1.11.2.1.1.3.1 = Counter32: 9026
.1.3.6.1.4.1.1602.1.11.2.1.1.3.17 = Counter32: 354
.1.3.6.1.4.1.1602.1.11.2.1.1.3.25 = Counter32: 8672
"""
STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 9026\n"
CANON_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "Canon MF642C/643C/644C /P"\n'


def _device(**overrides) -> MfpDevice:
    defaults = dict(
        name="Copy Room Canon",
        vendor="canon",
        connector_type="canon_department_id",
        ip_address="10.0.0.20",
    )
    defaults.update(overrides)
    return MfpDevice(**defaults)


def test_registered_in_connector_registry():
    assert CONNECTOR_REGISTRY["canon_department_id"] is CanonDepartmentIdConnector
    assert isinstance(get_connector("canon_department_id"), CanonDepartmentIdConnector)


def test_has_setup_notes():
    connector = CanonDepartmentIdConnector()
    assert connector.setup_notes
    assert "Department ID" in connector.setup_notes


@pytest.mark.asyncio
async def test_get_capabilities_reflects_known_canon_feature_set():
    connector = CanonDepartmentIdConnector()
    report = await connector.get_capabilities(_device())
    assert report.capabilities["department_id_accounting"] is True
    assert report.capabilities["api_accounting_retrieval"] is False
    assert report.capabilities["remote_user_provisioning"] is False


@pytest.mark.asyncio
async def test_get_meter_snapshot_reuses_verified_canon_snmp_breakdown():
    """Forces vendor_profile="canon" and reuses the same live-verified
    breakdown logic as app/printers/snmp_counters.py — not a
    reimplementation."""
    connector = CanonDepartmentIdConnector()
    responses = iter([STANDARD_TOTAL_OUTPUT, CANON_LABELS_OUTPUT, CANON_VALUES_OUTPUT])
    with patch("app.printers.snmp_counters._run_snmp", side_effect=lambda argv: next(responses)):
        snapshot = await connector.get_meter_snapshot(_device())
    assert snapshot.total == 9026
    assert snapshot.copy == 354
    assert snapshot.print == 8672
    assert snapshot.confidence == "verified"
    assert snapshot.vendor_profile_used == "canon"


@pytest.mark.asyncio
async def test_test_connection_forces_canon_profile_no_sysdescr_autodetect():
    connector = CanonDepartmentIdConnector()
    with patch(
        "app.printers.snmp_counters._run_snmp", return_value=CANON_SYS_DESCR_OUTPUT
    ) as mock_run:
        result = await connector.test_connection(_device())
    assert result.ok is True
    # Only one call (the connection check itself) — never a separate
    # sysDescr auto-detect call, since the vendor profile is already known.
    assert mock_run.call_count == 1


@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported():
    connector = CanonDepartmentIdConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.get_user_accounting(_device(), period=None)


@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported():
    connector = CanonDepartmentIdConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.sync_users_to_device(_device(), identities=[])


@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic():
    from app.models.copier_import import CopierImportTemplate

    connector = CanonDepartmentIdConnector()
    template = CopierImportTemplate(
        name="t",
        vendor="canon",
        column_mapping={"identity_value": "Dept ID", "occurred_at": "Date", "page_count": "Pages"},
        identity_type="department_id",
        delimiter=",",
    )
    csv_bytes = b"Dept ID,Date,Pages\n1001,2026-07-01,42\n"
    result = await connector.import_accounting_file(_device(), csv_bytes, template)
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "1001"
    assert result.rows[0].page_count == 42
