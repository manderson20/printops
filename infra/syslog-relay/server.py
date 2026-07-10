#!/usr/bin/env python3
"""UDP syslog collector for printers.

Listens for RFC 3164 (BSD syslog — what real-world MFPs actually speak;
Canon/HP/Konica Minolta/Kyocera event-notification exports all use this
older format, not RFC 5424) with an RFC 5424 fallback parser for anything
that happens to send the newer format. Parsed messages are batched
client-side and POSTed to printops-api's internal, backend-token-gated
POST /api/v1/internal/syslog/events — matching each message's source IP to
a Printer, applying the configured noise floor, and persisting it all
happens there (app/syslog/service.py), not in this process.

Runs as its own process/systemd service (printops-syslog-relay.service),
same reasoning as infra/ldap-relay/ for why this isn't folded into
printops-api's own event loop: UDP 514 is a privileged port, and granting
CAP_NET_BIND_SERVICE to a small, single-purpose process is a narrower
blast radius than granting it to the whole API service. Unlike the LDAP
relay, this doesn't need Twisted/ldaptor — UDP syslog has no
handshake/session state, so asyncio's own DatagramProtocol is enough.
"""

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("printops-syslog-relay")

API_BASE = os.environ.get("PRINTOPS_API_BASE", "http://localhost:8000")
ENV_FILE = os.environ.get("PRINTOPS_ENV_FILE", "/home/itadmin/printops/apps/api/.env")
PORT_ENV_OVERRIDE = os.environ.get("PRINTOPS_SYSLOG_PORT")
# Empty string (default) binds every interface, as a real deployment needs
# (printers reach this from other VLANs). Set to 127.0.0.1 for a
# loopback-only manual test that never exposes the service to the network.
BIND_INTERFACE = os.environ.get("PRINTOPS_SYSLOG_BIND_INTERFACE", "")
DEFAULT_PORT = 514

# Batched rather than one HTTP call per UDP packet — a print jam or a
# chatty firmware can burst many lines at once. Flushed on whichever comes
# first.
BATCH_MAX_EVENTS = 200
BATCH_MAX_SECONDS = 2.0

MONTHS = {
    name: i
    for i, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
    )
}

PRI_RE = re.compile(r"^<(\d{1,3})>(.*)$", re.DOTALL)
RFC3164_RE = re.compile(
    r"^(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<hh>\d{2}):(?P<mm>\d{2}):(?P<ss>\d{2})"
    r"\s+(?P<hostname>\S+)\s+(?P<rest>.*)$",
    re.DOTALL,
)
RFC3164_TAG_RE = re.compile(r"^(?P<app>[\w\-./]+?)(\[(?P<pid>\d+)\])?:\s?(?P<msg>.*)$", re.DOTALL)


def load_backend_token() -> str:
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("PRINTOPS_BACKEND_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError(f"PRINTOPS_BACKEND_TOKEN not found in {ENV_FILE}")


def _parse_pri(raw: str) -> tuple[int | None, str | None, str]:
    """Returns (facility, severity_name, remainder_after_pri). Facility and
    severity follow RFC 5424 §6.2.1's numbering (also what RFC 3164 uses,
    it just never named it that) — severity 0 is "emerg" through 7
    "debug", so `severity_order[pri & 7]` maps directly."""
    match = PRI_RE.match(raw)
    if not match:
        return None, None, raw
    pri = int(match.group(1))
    facility = pri >> 3
    severity_num = pri & 7
    severity_order = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
    severity = severity_order[severity_num] if 0 <= severity_num < 8 else None
    return facility, severity, match.group(2)


def _parse_rfc3164_timestamp(mon: str, day: str, hh: str, mm: str, ss: str) -> datetime | None:
    """RFC 3164 timestamps carry no year — this assumes the current year,
    which is wrong for roughly one day a year (a message logged in the
    last moments of December, received after midnight on Jan 1). Acceptable
    for a diagnostic tool; received_at (this relay's own clock, always
    correct) is what ordering/retention actually key off, not this field."""
    month = MONTHS.get(mon[:3].title())
    if month is None:
        return None
    try:
        year = datetime.now(UTC).year
        return datetime(year, month, int(day), int(hh), int(mm), int(ss), tzinfo=UTC)
    except ValueError:
        return None


def _parse_rfc3164(remainder: str) -> dict[str, Any]:
    match = RFC3164_RE.match(remainder.strip())
    if not match:
        return {"hostname": None, "app_name": None, "device_timestamp": None, "message": remainder.strip()}

    device_timestamp = _parse_rfc3164_timestamp(
        match.group("mon"), match.group("day"), match.group("hh"), match.group("mm"), match.group("ss")
    )
    rest = match.group("rest")
    hostname_field = match.group("hostname")

    if hostname_field.endswith(":"):
        # No HOSTNAME field was actually sent — this device went straight
        # to "TAG: MSG" (common; RFC 3164 treats HOSTNAME as optional in
        # practice even though the grammar lists it first). What the regex
        # captured as "hostname" is really the tag, and `rest` is already
        # the message with no further tag-stripping needed.
        return {
            "hostname": None,
            "app_name": hostname_field[:-1],
            "device_timestamp": device_timestamp,
            "message": rest,
        }

    tag_match = RFC3164_TAG_RE.match(rest)
    if tag_match:
        app_name = tag_match.group("app")
        message = tag_match.group("msg")
    else:
        app_name = None
        message = rest

    return {
        "hostname": hostname_field,
        "app_name": app_name,
        "device_timestamp": device_timestamp,
        "message": message,
    }


def _strip_structured_data(text: str) -> str:
    """Best-effort RFC 5424 STRUCTURED-DATA skip — "-" (no SD, the
    overwhelming common case for anything that sends 5424 at all) or a
    single bracketed SD-ELEMENT. Doesn't handle multiple concatenated
    SD-ELEMENTs or an escaped "]" inside a param value; real-world printer
    firmware isn't expected to send that level of detail."""
    text = text.strip()
    if text.startswith("-"):
        return text[1:].strip()
    if text.startswith("["):
        end = text.find("]")
        if end != -1:
            return text[end + 1 :].strip()
    return text


def _parse_rfc5424(remainder: str) -> dict[str, Any]:
    parts = remainder.strip().split(None, 5)
    # version, timestamp, hostname, app-name, procid, msgid+structured-data+msg
    if len(parts) < 6:
        return {"hostname": None, "app_name": None, "device_timestamp": None, "message": remainder.strip()}
    _version, timestamp_str, hostname, app_name, _procid, rest = parts
    try:
        device_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        device_timestamp = None

    # rest is "MSGID STRUCTURED-DATA MSG" — split off MSGID, then peel SD.
    msgid_rest = rest.split(None, 1)
    after_msgid = msgid_rest[1] if len(msgid_rest) > 1 else ""
    message = _strip_structured_data(after_msgid)

    return {
        "hostname": None if hostname == "-" else hostname,
        "app_name": None if app_name == "-" else app_name,
        "device_timestamp": device_timestamp,
        "message": message,
    }


def parse_syslog_message(raw: str, received_at: datetime, source_ip: str) -> dict[str, Any]:
    facility, severity, remainder = _parse_pri(raw)
    is_5424 = bool(re.match(r"^1\s+\d{4}-\d{2}-\d{2}T", remainder))
    parsed = _parse_rfc5424(remainder) if is_5424 else _parse_rfc3164(remainder)

    return {
        "source_ip": source_ip,
        "received_at": received_at.isoformat(),
        "device_timestamp": parsed["device_timestamp"].isoformat() if parsed["device_timestamp"] else None,
        "severity": severity,
        "facility": facility,
        "hostname": parsed["hostname"],
        "app_name": parsed["app_name"],
        "message": parsed["message"] or raw,
        "raw": raw,
    }


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_message):
        self._on_message = on_message

    def connection_made(self, transport):  # noqa: D102 - asyncio callback
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            raw = data.decode("utf-8", errors="replace").rstrip("\x00\r\n")
            event = parse_syslog_message(raw, datetime.now(UTC), addr[0])
        except Exception:
            logger.exception("Failed to parse syslog datagram from %s", addr[0])
            return
        self._on_message(event)

    def error_received(self, exc: Exception) -> None:
        logger.warning("Syslog UDP error: %s", exc)


async def _flush_loop(buffer: list[dict[str, Any]], client: httpx.AsyncClient) -> None:
    while True:
        await asyncio.sleep(BATCH_MAX_SECONDS)
        if not buffer:
            continue
        batch, buffer[:] = buffer[:], []
        await _post_batch(client, batch)


async def _post_batch(client: httpx.AsyncClient, batch: list[dict[str, Any]]) -> None:
    try:
        response = await client.post("/api/v1/internal/syslog/events", json={"events": batch})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Failed to post %d syslog event(s) to printops-api: %s", len(batch), exc)


async def main() -> None:
    token = load_backend_token()
    client = httpx.AsyncClient(base_url=API_BASE, headers={"X-Backend-Token": token}, timeout=10.0)

    buffer: list[dict[str, Any]] = []

    def on_message(event: dict[str, Any]) -> None:
        buffer.append(event)
        if len(buffer) >= BATCH_MAX_EVENTS:
            batch, buffer[:] = buffer[:], []
            asyncio.create_task(_post_batch(client, batch))

    port = int(PORT_ENV_OVERRIDE) if PORT_ENV_OVERRIDE else DEFAULT_PORT
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: SyslogProtocol(on_message),
        local_addr=(BIND_INTERFACE or "0.0.0.0", port),
    )
    logger.info("printops-syslog-relay listening on %s:%d (UDP)", BIND_INTERFACE or "0.0.0.0", port)

    flush_task = asyncio.create_task(_flush_loop(buffer, client))
    try:
        await asyncio.Future()  # run forever
    finally:
        flush_task.cancel()
        transport.close()
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
