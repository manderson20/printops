from unittest.mock import patch

import pytest

from app.copiers.connector import CapabilityNotSupported
from app.copiers.konica_bizhub import KonicaBizhubConnector
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

# Same live-captured fixtures as tests/test_snmp_counters.py — real
# snmpwalk/snmpget -On output against a real Konica Minolta bizhub 750i.
STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 9026\n"
KONICA_TOTAL = "INTEGER: 757288"
KONICA_METER_A = "INTEGER: 533574"
KONICA_METER_B = "INTEGER: 223714"
KONICA_SYS_DESCR_OUTPUT = '.1.3.6.1.2.1.1.1.0 = STRING: "KONICA MINOLTA bizhub 750i"\n'


def _device(**overrides) -> MfpDevice:
    defaults = dict(
        name="Copy Room Konica", vendor="konica_minolta", connector_type="konica_bizhub", ip_address="10.0.0.30"
    )
    defaults.update(overrides)
    return MfpDevice(**defaults)


def test_registered_in_connector_registry():
    assert CONNECTOR_REGISTRY["konica_bizhub"] is KonicaBizhubConnector
    assert isinstance(get_connector("konica_bizhub"), KonicaBizhubConnector)


def test_has_setup_notes():
    connector = KonicaBizhubConnector()
    assert connector.setup_notes
    assert "Account Track" in connector.setup_notes


@pytest.mark.asyncio
async def test_get_capabilities_reflects_known_konica_feature_set():
    connector = KonicaBizhubConnector()
    report = await connector.get_capabilities(_device())
    assert report.capabilities["department_id_accounting"] is True
    assert report.capabilities["user_code_pin_auth"] is True
    assert report.capabilities["api_accounting_retrieval"] is False


@pytest.mark.asyncio
async def test_get_meter_snapshot_reuses_verified_konica_snmp_breakdown_and_stays_best_effort():
    """Forces vendor_profile="konica_minolta" and reuses the same
    live-verified breakdown logic as app/printers/snmp_counters.py — the
    confidence stays "best_effort" (never upgraded), matching that
    module's own honesty about the copy/print split not being confirmed
    against an official MIB."""
    connector = KonicaBizhubConnector()
    responses = iter([STANDARD_TOTAL_OUTPUT, KONICA_TOTAL, KONICA_METER_A, KONICA_METER_B])
    with patch("app.printers.snmp_counters._run_snmp", side_effect=lambda argv: next(responses)):
        snapshot = await connector.get_meter_snapshot(_device())
    assert snapshot.total == 9026
    assert snapshot.copy == 533574
    assert snapshot.print == 223714
    assert snapshot.confidence == "best_effort"
    assert snapshot.vendor_profile_used == "konica_minolta"


@pytest.mark.asyncio
async def test_test_connection_forces_konica_profile_no_sysdescr_autodetect():
    connector = KonicaBizhubConnector()
    with patch("app.printers.snmp_counters._run_snmp", return_value=KONICA_SYS_DESCR_OUTPUT) as mock_run:
        result = await connector.test_connection(_device())
    assert result.ok is True
    assert mock_run.call_count == 1


@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported():
    connector = KonicaBizhubConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.get_user_accounting(_device(), period=None)


@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported():
    connector = KonicaBizhubConnector()
    with pytest.raises(CapabilityNotSupported):
        await connector.sync_users_to_device(_device(), identities=[])


@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic():
    connector = KonicaBizhubConnector()
    template = CopierImportTemplate(
        name="t",
        vendor="konica_minolta",
        column_mapping={"identity_value": "Account Name", "occurred_at": "Date", "page_count": "Pages"},
        identity_type="department_id",
        delimiter=",",
    )
    csv_bytes = b"Account Name,Date,Pages\nFrontOffice,2026-07-01,42\n"
    result = await connector.import_accounting_file(_device(), csv_bytes, template)
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "FrontOffice"
    assert result.rows[0].page_count == 42
