from unittest.mock import patch

import pytest

from app.copiers.connector import CapabilityNotSupported
from app.copiers.registry import CONNECTOR_REGISTRY, get_connector
from app.copiers.vendor_placeholders import (
    HpAccessControlConnector,
    LexmarkAccountingConnector,
    SharpAccountingConnector,
)
from app.models.copier_import import CopierImportTemplate
from app.models.mfp_device import MfpDevice

STANDARD_TOTAL_OUTPUT = ".1.3.6.1.2.1.43.10.2.1.4.1.1 = Counter32: 12345\n"

ALL_PLACEHOLDERS = [
    ("lexmark_accounting", LexmarkAccountingConnector),
    ("hp_access_control", HpAccessControlConnector),
    ("sharp_accounting", SharpAccountingConnector),
]


def _device(connector_type: str) -> MfpDevice:
    return MfpDevice(name="t", connector_type=connector_type, ip_address="10.0.0.40")


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
def test_registered_in_connector_registry(connector_type, connector_cls):
    assert CONNECTOR_REGISTRY[connector_type] is connector_cls
    assert isinstance(get_connector(connector_type), connector_cls)


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
def test_has_setup_notes_explaining_limitations(connector_type, connector_cls):
    notes = connector_cls().setup_notes
    assert notes
    assert "not" in notes.lower() or "no confirmed" in notes.lower()


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
@pytest.mark.asyncio
async def test_get_capabilities_not_auto_detected(connector_type, connector_cls):
    """Unlike Canon/Konica/Kyocera/Ricoh/Xerox, these are genuinely
    unconfirmed — the connector doesn't claim to know the device's
    capabilities."""
    with pytest.raises(CapabilityNotSupported):
        await connector_cls().get_capabilities(_device(connector_type))


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
@pytest.mark.asyncio
async def test_get_user_accounting_honestly_unsupported(connector_type, connector_cls):
    with pytest.raises(CapabilityNotSupported):
        await connector_cls().get_user_accounting(_device(connector_type), period=None)


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
@pytest.mark.asyncio
async def test_sync_users_to_device_honestly_unsupported(connector_type, connector_cls):
    with pytest.raises(CapabilityNotSupported):
        await connector_cls().sync_users_to_device(_device(connector_type), identities=[])


@pytest.mark.asyncio
async def test_lexmark_meter_uses_existing_unsupported_breakdown_entry():
    """Lexmark already has a VENDOR_BREAKDOWN_FNS entry in
    snmp_counters.py (honestly "unsupported" confidence — no real
    hardware verified yet) — the placeholder reuses it rather than
    reimplementing anything."""
    with patch("app.printers.snmp_counters._run_snmp", return_value=STANDARD_TOTAL_OUTPUT):
        snapshot = await LexmarkAccountingConnector().get_meter_snapshot(
            _device("lexmark_accounting")
        )
    assert snapshot.total == 12345
    assert snapshot.copy is None
    assert snapshot.print is None
    assert snapshot.confidence == "unsupported"
    assert snapshot.vendor_profile_used == "lexmark"


@pytest.mark.asyncio
async def test_sharp_meter_falls_back_to_standard_total_only():
    """Sharp has no VENDOR_BREAKDOWN_FNS entry at all — falls back to the
    generic (total-only, unsupported-confidence) behavior, never a
    fabricated copy/print split."""
    with patch("app.printers.snmp_counters._run_snmp", return_value=STANDARD_TOTAL_OUTPUT):
        snapshot = await SharpAccountingConnector().get_meter_snapshot(_device("sharp_accounting"))
    assert snapshot.total == 12345
    assert snapshot.copy is None
    assert snapshot.print is None
    assert snapshot.confidence == "unsupported"
    assert snapshot.vendor_profile_used == "sharp"


@pytest.mark.parametrize("connector_type,connector_cls", ALL_PLACEHOLDERS)
@pytest.mark.asyncio
async def test_import_accounting_file_uses_same_csv_pipeline_as_generic(
    connector_type, connector_cls
):
    template = CopierImportTemplate(
        name="t",
        vendor="generic",
        column_mapping={"identity_value": "User", "occurred_at": "Date", "page_count": "Pages"},
        identity_type="user_code",
        delimiter=",",
    )
    csv_bytes = b"User,Date,Pages\nabc123,2026-07-01,10\n"
    result = await connector_cls().import_accounting_file(
        _device(connector_type), csv_bytes, template
    )
    assert len(result.rows) == 1
    assert result.rows[0].external_identity_used == "abc123"
    assert result.rows[0].page_count == 10
