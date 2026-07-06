from unittest.mock import patch

import pytest

from app.copiers.connector import CapabilityNotSupported
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.copiers.ricoh_user_code_auth import RicohUserCodeAuthConnector
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 5500\n"
RICOH_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "RICOH MP C3004"\n'


def _device(**overrides) -> MfpDevice:
    defaults = dict(
        name="Copy Room Ricoh",
        vendor="ricoh",
        connector_type="ricoh_user_code_auth",
        ip_address="10.0.0.60",
    )
    defaults.update(overrides)
    return MfpDevice(**defaults)


def test_registered_in_connector_registry():
    assert CONNECTOR_REGISTRY["ricoh_user_code_auth"] is RicohUserCodeAuthConnector
    assert isinstance(get_connector("ricoh_user_code_auth"), RicohUserCodeAuthConnector)


def test_has_setup_notes():
    connector = RicohUserCodeAuthConnector()
    assert connector.setup_notes
    assert "User Code" in connector.setup_notes


@pytest.mark.asyncio
async def test_get_capabilities_reflects_known_ricoh_feature_set():
    connector = RicohUserCodeAuthConnector()
    report = await connector.get_capabilities(_device())
    assert report.capabilities["user_code_pin_auth"] is True
    assert report.capabilities["api_accounting_retrieval"] is False
    # Deliberately not claimed — needs a directory server PrintOps doesn't
    # integrate with.
    assert report.capabilities["ldap_auth"] is False


@pytest.mark.asyncio
async def test_get_meter_snapshot_falls_back_to_standard_total_only():
    """No Ricoh entry in VENDOR_BREAKDOWN_FNS — total-only, never a
    fabricated copy/print split."""
    connector = RicohUserCodeAuthConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=STANDARD_TOTAL_OUTPUT):
        snapshot = await connector.get_meter_snapshot(_device())
    assert snapshot.total == 5500
    assert snapshot.copy is None
    assert snapshot.print is None
    assert snapshot.confidence == "unsupported"
    assert snapshot.vendor_profile_used == "ricoh"


@pytest.mark.asyncio
async def test_test_connection_forces_ricoh_profile():
    connector = RicohUserCodeAuthConnector()
    with patch(
        "app.printers.snmp_counters._run_snmp", return_value=RICOH_SYS_DESCR_OUTPUT
    ) as mock_run:
        result = await connector.test_connection(_device())
    assert result.ok is True
    assert mock_run.call_count == 1


@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported():
    connector = RicohUserCodeAuthConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.get_user_accounting(_device(), period=None)


@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported():
    connector = RicohUserCodeAuthConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.sync_users_to_device(_device(), identities=[])


@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic():
    connector = RicohUserCodeAuthConnector()
    template = CopierImportTemplate(
        name="t",
        vendor="ricoh",
        column_mapping={
            "identity_value": "User Code",
            "occurred_at": "Date",
            "page_count": "Pages",
        },
        identity_type="user_code",
        delimiter=",",
    )
    csv_bytes = b"User Code,Date,Pages\n5551,2026-07-01,7\n"
    result = await connector.import_accounting_file(_device(), csv_bytes, template)
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "5551"
    assert result.rows[0].page_count == 7
