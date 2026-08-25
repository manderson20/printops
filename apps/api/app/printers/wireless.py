"""Whether a printer is running its own Wi-Fi network.

Many printers can host an access point of their own — HP calls it Wi-Fi
Direct, Canon calls it Access Point Mode — and ship with it **on**. That is a
way onto a machine that does not pass through the district's network at all:
no VLAN, no ACL, no PrintOps. The only way anyone here knew about it was
walking the building watching the Wi-Fi list on a phone.

The radio itself never appears on the wired network, but the device describes
it in the standard MIB-II interface table (RFC 2863) and says whether it is
running. That is the whole signal, and it costs two SNMP walks on a poll this
server already makes.

**Two radios, two different meanings.** A printer with wireless usually
reports both a client radio (`wifi0`, `wlan0`, `Wlan`) and an access-point
radio (`wifiUAP`, `wlan1`, `UAP`). The client radio being up means the printer
has *joined* a network — worth knowing, and a separate question. The access
point being up is the one that means "this machine is broadcasting an SSID
right now". Measured across this fleet on 2026-08-25: six printers, all HP,
with the client radio down and the AP radio up — each running its own little
network nobody had asked for.

**The SSID name is usually not readable**, so this deliberately does not
promise it. Only some devices implement the IEEE 802.11 MIB; of the HPs here,
one answered `dot11DesiredSSID` as the literal string "unconfigured" and
another as empty. Reporting "broadcasting, name unknown" is honest; inventing
a name from the model would not be.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.printer import Printer
from app.models.snmp import SnmpDefaultsSettings
from app.printers.snmp_counters import (
    SnmpConfig,
    SnmpProbeError,
    resolve_snmp_config,
    snmp_walk,
)

logger = logging.getLogger(__name__)

IF_DESCR_OID = "1.3.6.1.2.1.2.2.1.2"
IF_OPER_STATUS_OID = "1.3.6.1.2.1.2.2.1.8"

# RFC 2863 ifOperStatus. 1 is up; everything else (down, testing, dormant,
# notPresent, lowerLayerDown) is not broadcasting.
IF_OPER_UP = 1

# Interface names that mean a radio. Matched loosely because every vendor
# spells it differently — wifi0/wifiUAP on HP's small LaserJets, wlan0/wlan1 on
# the bigger ones, Wlan/UAP on Canon, and ra0/ath0 on some OEM boards.
_WIRELESS_NAME = re.compile(r"wlan|wifi|wi-fi|wireless|uap|ath\d|ra\d|wl\d", re.IGNORECASE)

# Of those, the ones that are the printer's *own* access point rather than a
# client radio joined to somebody else's network. "wlan1" is included because
# every dual-radio device seen here uses index 0 for the client and 1 for the
# AP; a device that breaks that convention reads as a client radio, which errs
# toward not accusing a printer of broadcasting.
_ACCESS_POINT_NAME = re.compile(r"uap|direct|wlan1|wifi1", re.IGNORECASE)

_STRING_VALUE = re.compile(r'STRING:\s*"?(.*?)"?\s*$')
_INTEGER_VALUE = re.compile(r"INTEGER:\s*(-?\d+)")


@dataclass(frozen=True)
class WirelessRadio:
    name: str
    up: bool
    # True when this is the printer hosting a network, rather than joining one.
    access_point: bool

    def as_dict(self) -> dict:
        return {"name": self.name, "up": self.up, "access_point": self.access_point}


@dataclass(frozen=True)
class WirelessState:
    radios: list[WirelessRadio] = field(default_factory=list)
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.error is None

    @property
    def broadcasting(self) -> bool | None:
        """True when an access-point radio is up — the printer is hosting a
        network right now. False when it has radios and none of them is doing
        that, or has no radio at all. None when the device could not be asked,
        which must not be shown as "no": a printer that stopped answering SNMP
        is not a printer that stopped broadcasting."""
        if not self.known:
            return None
        return any(radio.up and radio.access_point for radio in self.radios)

    @property
    def joined_a_network(self) -> bool | None:
        """True when a client radio is up: the printer is on somebody's Wi-Fi
        as well as (or instead of) the wire. Not the same question as
        broadcasting, and worth knowing separately — a printer bridging two
        networks is its own thing to look at."""
        if not self.known:
            return None
        return any(radio.up and not radio.access_point for radio in self.radios)


def _string(raw: str) -> str:
    match = _STRING_VALUE.search(raw)
    return match.group(1) if match else raw.strip()


def _integer(raw: str) -> int | None:
    match = _INTEGER_VALUE.search(raw)
    return int(match.group(1)) if match else None


def parse_radios(descriptions: dict[str, str], statuses: dict[str, str]) -> list[WirelessRadio]:
    radios = []
    for index, raw in descriptions.items():
        name = _string(raw)
        if not name or not _WIRELESS_NAME.search(name):
            continue
        radios.append(
            WirelessRadio(
                name=name,
                up=_integer(statuses.get(index, "")) == IF_OPER_UP,
                access_point=bool(_ACCESS_POINT_NAME.search(name)),
            )
        )
    return radios


def probe_wireless(ip_address: str, config: SnmpConfig) -> WirelessState:
    """Reads the device's interface table and picks out its radios.

    Read-only, and nothing here is specific to a make or model — the interface
    table is standard MIB-II and every device that has a radio describes it
    there. A device with no wireless hardware simply has no matching row, which
    is a real answer (`broadcasting` False) rather than an unknown.
    """
    try:
        descriptions = snmp_walk(ip_address, IF_DESCR_OID, config)
        statuses = snmp_walk(ip_address, IF_OPER_STATUS_OID, config)
    except SnmpProbeError as exc:
        return WirelessState(error=str(exc))
    return WirelessState(radios=parse_radios(descriptions, statuses))


async def refresh_printer_wireless(printer: Printer, defaults: SnmpDefaultsSettings) -> None:
    """Updates `printer`'s wireless_* fields in place. Does not commit —
    caller owns the transaction, matching refresh_printer_counters above.

    On a failed probe the error is recorded and the previous radio list is left
    alone, the same convention page_count_error follows: a transient SNMP
    hiccup should not erase what was last known.

    The kept reading is history, not a current answer, and callers must not
    present it as one. `wireless_error` being set is what says so — the
    Printers page reads it first and shows "Not reported" with the date of the
    last reading, because a printer last seen with its access point off can
    switch it on and *then* stop answering, and a page that went on saying
    "Off" would be failing exactly the person relying on it."""
    if not printer.snmp_enabled or not printer.ip_address:
        return
    state = await asyncio.to_thread(
        probe_wireless, printer.ip_address, resolve_snmp_config(printer, defaults)
    )
    printer.wireless_checked_at = datetime.now(UTC)
    if not state.known:
        printer.wireless_error = state.error
        return
    printer.wireless_error = None
    printer.wireless_broadcasting = state.broadcasting
    printer.wireless_radios = [radio.as_dict() for radio in state.radios]
