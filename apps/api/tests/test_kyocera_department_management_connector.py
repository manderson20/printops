from unittest.mock import patch

import pytest

from app.copiers.connector import CapabilityNotSupported
from app.copiers.kyocera_department_management import KyoceraDepartmentManagementConnector
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 9026\n"
KYOCERA_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "KYOCERA TASKalfa 3253ci"\n'


def _device(**overrides) -> MfpDevice:
    defaults = dict(
        name="Copy Room Kyocera",
        vendor="kyocera",
        connector_type="kyocera_department_management",
        ip_address="10.0.0.50",
    )
    defaults.update(overrides)
    return MfpDevice(**defaults)


def test_registered_in_connector_registry():
    assert CONNECTOR_REGISTRY["kyocera_department_management"] is KyoceraDepartmentManagementConnector
    assert isinstance(get_connector("kyocera_department_management"), KyoceraDepartmentManagementConnector)


def test_has_setup_notes():
    connector = KyoceraDepartmentManagementConnector()
    assert connector.setup_notes
    assert "Job Accounting" in connector.setup_notes


@pytest.mark.asyncio
async def test_get_capabilities_reflects_known_kyocera_feature_set():
    connector = KyoceraDepartmentManagementConnector()
    report = await connector.get_capabilities(_device())
    assert report.capabilities["department_id_accounting"] is True
    assert report.capabilities["user_code_pin_auth"] is True
    assert report.capabilities["api_accounting_retrieval"] is False


@pytest.mark.asyncio
async def test_get_meter_snapshot_stays_unsupported_confidence():
    """Kyocera's existing VENDOR_BREAKDOWN_FNS entry is just the
    unsupported fallback (no real hardware verified yet) — the connector
    reports total-only, never a fabricated split."""
    connector = KyoceraDepartmentManagementConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=STANDARD_TOTAL_OUTPUT):
        snapshot = await connector.get_meter_snapshot(_device())
    assert snapshot.total == 9026
    assert snapshot.copy is None
    assert snapshot.print is None
    assert snapshot.confidence == "unsupported"
    assert snapshot.vendor_profile_used == "kyocera"


@pytest.mark.asyncio
async def test_test_connection_forces_kyocera_profile():
    connector = KyoceraDepartmentManagementConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=KYOCERA_SYS_DESCR_OUTPUT) as mock_run:
        result = await connector.test_connection(_device())
    assert result.ok is True
    assert mock_run.call_count == 1


@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported():
    connector = KyoceraDepartmentManagementConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.get_user_accounting(_device(), period=None)


@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported():
    connector = KyoceraDepartmentManagementConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.sync_users_to_device(_device(), identities=[])


@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic():
    connector = KyoceraDepartmentManagementConnector()
    template = CopierImportTemplate(
        name="t",
        vendor="kyocera",
        column_mapping={"identity_value": "Account", "occurred_at": "Date", "page_count": "Pages"},
        identity_type="department_id",
        delimiter=",",
    )
    csv_bytes = b"Account,Date,Pages\n2001,2026-07-01,42\n"
    result = await connector.import_accounting_file(_device(), csv_bytes, template)
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "2001"
    assert result.rows[0].page_count == 42
