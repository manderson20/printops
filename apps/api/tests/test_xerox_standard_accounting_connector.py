from unittest.mock import patch

import pytest

from app.copiers.connector import CapabilityNotSupported
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.copiers.xerox_standard_accounting import XeroxStandardAccountingConnector
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 8800\n"
XEROX_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "Xerox VersaLink C405"\n'


def _device(**overrides) -> MfpDevice:
    defaults = dict(
        name="Copy Room Xerox",
        vendor="xerox",
        connector_type="xerox_standard_accounting",
        ip_address="10.0.0.70",
    )
    defaults.update(overrides)
    return MfpDevice(**defaults)


def test_registered_in_connector_registry():
    assert CONNECTOR_REGISTRY["xerox_standard_accounting"] is XeroxStandardAccountingConnector
    assert isinstance(get_connector("xerox_standard_accounting"), XeroxStandardAccountingConnector)


def test_has_setup_notes():
    connector = XeroxStandardAccountingConnector()
    assert connector.setup_notes
    assert "Standard Accounting" in connector.setup_notes


@pytest.mark.asyncio
async def test_get_capabilities_reflects_known_xerox_feature_set():
    connector = XeroxStandardAccountingConnector()
    report = await connector.get_capabilities(_device())
    assert report.capabilities["user_code_pin_auth"] is True  # User ID
    assert report.capabilities["department_id_accounting"] is True  # Account ID
    assert report.capabilities["api_accounting_retrieval"] is False


@pytest.mark.asyncio
async def test_get_meter_snapshot_falls_back_to_standard_total_only():
    """No Xerox entry in VENDOR_BREAKDOWN_FNS — total-only, never a
    fabricated copy/print split."""
    connector = XeroxStandardAccountingConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=STANDARD_TOTAL_OUTPUT):
        snapshot = await connector.get_meter_snapshot(_device())
    assert snapshot.total == 8800
    assert snapshot.copy is None
    assert snapshot.print is None
    assert snapshot.confidence == "unsupported"
    assert snapshot.vendor_profile_used == "xerox"


@pytest.mark.asyncio
async def test_test_connection_forces_xerox_profile():
    connector = XeroxStandardAccountingConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=XEROX_SYS_DESCR_OUTPUT) as mock_run:
        result = await connector.test_connection(_device())
    assert result.ok is True
    assert mock_run.call_count == 1


@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported():
    connector = XeroxStandardAccountingConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.get_user_accounting(_device(), period=None)


@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported():
    connector = XeroxStandardAccountingConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.sync_users_to_device(_device(), identities=[])


@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic():
    connector = XeroxStandardAccountingConnector()
    template = CopierImportTemplate(
        name="t",
        vendor="xerox",
        column_mapping={"identity_value": "User ID", "occurred_at": "Date", "page_count": "Pages"},
        identity_type="vendor_user_id",
        delimiter=",",
    )
    csv_bytes = b"User ID,Date,Pages\njsmith,2026-07-01,15\n"
    result = await connector.import_accounting_file(_device(), csv_bytes, template)
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "jsmith"
    assert result.rows[0].page_count == 15
