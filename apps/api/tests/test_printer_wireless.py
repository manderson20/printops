"""Finding a printer that is running a Wi-Fi network of its own.

The distinction that carries the whole feature: a printer with wireless
reports two radios, and they mean opposite things. The client radio being up
means it has *joined* a network. The access point being up means it is
*hosting* one — a way onto the machine that never touches the district's
network, no VLAN and no ACL in the way.

The other thing to get right is the difference between "no" and "couldn't
ask". A printer that stopped answering SNMP is not a printer that stopped
broadcasting, and reporting the second as the first would quietly retire an
open door from the admin's list.
"""

from unittest.mock import patch

from app.models.printer import Printer
from app.models.snmp import SnmpDefaultsSettings
from app.printers.snmp_counters import SnmpProbeError
from app.printers.wireless import (
    WirelessRadio,
    WirelessState,
    parse_radios,
    probe_wireless,
    refresh_printer_wireless,
)

# Straight from the fleet on 2026-08-25: an HP LaserJet M207-M212 with its
# client radio down and its access point up, which is how six of them shipped.
HP_SMALL = (
    {"1": 'STRING: "lo"', "2": 'STRING: "eth0"', "3": 'STRING: "wifi0"', "4": 'STRING: "wifiUAP"'},
    {"1": "INTEGER: 1", "2": "INTEGER: 1", "3": "INTEGER: 2", "4": "INTEGER: 1"},
)
CANON = (
    {"1": 'STRING: "FastEthernet"', "3": 'STRING: "Wlan"', "4": 'STRING: "UAP"'},
    {"1": "INTEGER: 1", "3": "INTEGER: 2", "4": "INTEGER: 2"},
)
WIRED_ONLY = (
    {"1": 'STRING: "eth0"', "2": 'STRING: "lo"'},
    {"1": "INTEGER: 1", "2": "INTEGER: 1"},
)


def test_an_access_point_that_is_up_is_broadcasting():
    state = WirelessState(radios=parse_radios(*HP_SMALL))
    assert [r.name for r in state.radios] == ["wifi0", "wifiUAP"]
    assert state.broadcasting is True
    # The client radio is down, so it has not joined anything — it is only
    # serving its own network.
    assert state.joined_a_network is False


def test_radios_that_are_all_down_are_not_broadcasting():
    state = WirelessState(radios=parse_radios(*CANON))
    assert state.broadcasting is False


def test_a_printer_with_no_radio_is_a_real_no_not_an_unknown():
    state = WirelessState(radios=parse_radios(*WIRED_ONLY))
    assert state.radios == []
    assert state.broadcasting is False


def test_a_client_radio_up_is_not_broadcasting():
    # The Athletic Office Canon: joined to a wireless network while also on the
    # wire. Worth knowing, but it is not hosting anything, and calling it
    # "broadcasting" would send someone to the wrong switch on the panel.
    descriptions = {"1": 'STRING: "FastEthernet"', "3": 'STRING: "Wlan"', "4": 'STRING: "UAP"'}
    statuses = {"1": "INTEGER: 1", "3": "INTEGER: 1", "4": "INTEGER: 2"}
    state = WirelessState(radios=parse_radios(descriptions, statuses))
    assert state.broadcasting is False
    assert state.joined_a_network is True


def test_every_vendors_spelling_is_recognised():
    for name in ("wlan1", "wifiUAP", "UAP", "WiFi-Direct", "wlan0", "Wlan", "ra0", "ath0"):
        radios = parse_radios({"1": f'STRING: "{name}"'}, {"1": "INTEGER: 1"})
        assert [r.name for r in radios] == [name], name


def test_ordinary_interfaces_are_not_mistaken_for_radios():
    for name in ("eth0", "lo", "FastEthernet", "Loopback", "EEPS2 Hard Ver.1.00"):
        assert parse_radios({"1": f'STRING: "{name}"'}, {"1": "INTEGER: 1"}) == [], name


def test_an_unreachable_device_is_unknown_not_off():
    with patch(
        "app.printers.wireless.snmp_walk", side_effect=SnmpProbeError("Timeout: No Response")
    ):
        state = probe_wireless("10.0.0.1", object())
    assert state.known is False
    assert state.broadcasting is None


# --- what gets written to the row -------------------------------------------


def _printer(**kwargs) -> Printer:
    kwargs.setdefault("snmp_enabled", True)
    return Printer(name="MS RM 241 Printer", ip_address="10.20.1.1", **kwargs)


async def test_a_successful_probe_records_the_radios_and_the_verdict():
    printer = _printer()
    with patch(
        "app.printers.wireless.probe_wireless",
        return_value=WirelessState(radios=parse_radios(*HP_SMALL)),
    ):
        await refresh_printer_wireless(printer, SnmpDefaultsSettings())
    assert printer.wireless_broadcasting is True
    assert printer.wireless_radios == [
        {"name": "wifi0", "up": False, "access_point": False},
        {"name": "wifiUAP", "up": True, "access_point": True},
    ]
    assert printer.wireless_checked_at is not None
    assert printer.wireless_error is None


async def test_a_failed_probe_keeps_the_last_known_answer():
    # Same convention as page_count_error: a transient SNMP hiccup records the
    # error and leaves the previous reading alone. Flipping this printer to
    # "not broadcasting" would take a genuinely open door off the admin's list.
    printer = _printer(
        wireless_broadcasting=True,
        wireless_radios=[{"name": "wifiUAP", "up": True, "access_point": True}],
    )
    with patch(
        "app.printers.wireless.probe_wireless",
        return_value=WirelessState(error="Timeout: No Response from 10.20.1.1:161"),
    ):
        await refresh_printer_wireless(printer, SnmpDefaultsSettings())
    assert printer.wireless_broadcasting is True
    assert printer.wireless_radios == [{"name": "wifiUAP", "up": True, "access_point": True}]
    assert printer.wireless_error is not None


async def test_a_printer_with_snmp_off_is_not_probed():
    printer = _printer(snmp_enabled=False)
    with patch("app.printers.wireless.probe_wireless") as probe:
        await refresh_printer_wireless(printer, SnmpDefaultsSettings())
    probe.assert_not_called()
    assert printer.wireless_checked_at is None


async def test_a_radio_switched_off_since_the_last_poll_is_recorded():
    printer = _printer(
        wireless_broadcasting=True,
        wireless_radios=[{"name": "wifiUAP", "up": True, "access_point": True}],
    )
    with patch(
        "app.printers.wireless.probe_wireless",
        return_value=WirelessState(radios=[WirelessRadio("wifiUAP", up=False, access_point=True)]),
    ):
        await refresh_printer_wireless(printer, SnmpDefaultsSettings())
    # The point of re-polling: someone walks over, turns Wi-Fi Direct off, and
    # the fleet view stops accusing that printer within the half hour.
    assert printer.wireless_broadcasting is False
