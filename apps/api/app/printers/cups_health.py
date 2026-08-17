"""How much room cupsd has left.

Background maintenance must never be the reason nobody can print. cupsd
serves every client — local `lp`, the Windows and Mac spoolers, PrintOps'
own lpadmin calls — out of one MaxClients pool, and when that pool is full
it holds new connections rather than refusing them, so a saturated server
looks to a user exactly like a printer that has stopped responding.

This module is the check that keeps PrintOps out of that pool when it is
running low. It reads /proc/net/unix rather than asking cupsd, deliberately:
asking would itself need a connection, which is the resource in question,
and a saturated scheduler is precisely when that request would hang.

Written after a flapping printer (ES-MS Nurse Copier, 10.10.3.5) triggered
137 full queue resyncs in a day, each one leaving cupsd holding a dead
socket, until all 100 slots were gone and every Windows client in the
district sat on "waiting for communication" for hours.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CUPS_SOCKET = "/run/cups/cups.sock"
PROC_NET_UNIX = Path("/proc/net/unix")
CUPSD_CONF = Path("/etc/cups/cupsd.conf")

# CUPS' own default when the directive is absent (cupsd.conf(5)).
DEFAULT_MAX_CLIENTS = 100

# Leave a comfortable margin: PrintOps backing off at 60% means real users
# still have 40 slots when it decides to stay out of the way. The cost of
# being wrong in this direction is a delayed queue resync; in the other, a
# building that cannot print.
SATURATION_RATIO = 0.6

_MAX_CLIENTS_RE = re.compile(r"^\s*MaxClients\s+(\d+)", re.IGNORECASE | re.MULTILINE)

# /proc/net/unix state column: 03 = connected, 01 = listening.
_STATE_CONNECTED = "03"


def max_clients() -> int:
    """cupsd's configured connection ceiling."""
    try:
        match = _MAX_CLIENTS_RE.search(CUPSD_CONF.read_text())
    except OSError:
        return DEFAULT_MAX_CLIENTS
    if match is None:
        return DEFAULT_MAX_CLIENTS
    try:
        value = int(match.group(1))
    except ValueError:
        return DEFAULT_MAX_CLIENTS
    return value or DEFAULT_MAX_CLIENTS


def open_connections() -> int | None:
    """Connections currently open to the CUPS domain socket, or None when
    that can't be determined (a non-Linux host, or /proc unreadable) —
    which callers must treat as "no reason to hold back" rather than as
    saturation, or an unreadable /proc would silently stop all queue
    maintenance forever."""
    try:
        lines = PROC_NET_UNIX.read_text().splitlines()
    except OSError:
        return None
    count = 0
    for line in lines:
        parts = line.split()
        # Num RefCount Protocol Flags Type St Inode Path
        if len(parts) < 8 or parts[-1] != CUPS_SOCKET:
            continue
        if parts[5] == _STATE_CONNECTED:
            count += 1
    return count


def is_saturated() -> bool:
    """True when cupsd is close enough to MaxClients that PrintOps should
    keep its own maintenance work out of the pool."""
    count = open_connections()
    if count is None:
        return False
    limit = max_clients()
    saturated = count >= limit * SATURATION_RATIO
    if saturated:
        logger.warning(
            "cupsd has %s of %s client slots in use — holding off on background "
            "queue maintenance so real print clients keep theirs.",
            count,
            limit,
        )
    return saturated
