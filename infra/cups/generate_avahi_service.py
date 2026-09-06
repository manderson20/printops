#!/usr/bin/env python3
"""Generates (or removes) a static Avahi service file for a printer's
AirPrint/Bonjour advertisement.

Static Avahi service files rather than cupsd's own DNS-SD publishing, because
what cupsd offers is all-or-nothing: it advertises every *shared* queue, and
every PrintOps queue is shared unconditionally (CUPS refuses network job
submission to one that isn't). There is no per-queue "advertise this one" to
map airprint_enabled onto. Drop an XML file in /etc/avahi/services/ and
avahi-daemon picks it up itself (inotify-watched, no restart needed).

This used to say the mechanism existed because cupsd's dnssd publishing did
not work on this box at all, confirmed at the time by debug logging. It works
now, and that was the whole of #110: cupsd advertised all 53 queues while
PrintOps published none and the UI reported every one of them as Hidden.
cupsd's publishing has to stay off for this file to be the thing that decides.
See infra/cups/README.md.

Usage: generate_avahi_service.py <printer-id>

Run as root (it writes to /etc/avahi/services/); called from
scripts/sync_cups_queue.sh, not usually invoked directly.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

API_BASE = "http://localhost:8000"
ENV_FILE = "/home/itadmin/printops/apps/api/.env"
SERVICES_DIR = "/etc/avahi/services"

DEFAULT_FORMATS = ["application/pdf", "image/urf", "image/jpeg"]

# One mDNS TXT string is capped at 255 bytes (RFC 6763 6.1), and avahi does not
# truncate an oversized one — it rejects the *entire service group*, logs
# "Invalid record", and publishes nothing at all for that printer.
#
# Found live. Two printers reporting 15 document formats produced a 456-byte
# pdl record, so both were silently absent from the network while this script
# reported "Wrote ..." for each of them. Nothing upstream noticed, because
# cupsd was advertising every queue itself until #110 — which is exactly what
# made a latent bug here into two printers nobody could find.
MAX_TXT_BYTES = 255

# Kept ahead of everything else when the list has to be cut. image/urf is what
# makes a queue an AirPrint destination at all, and pdf/jpeg are what a client
# will actually send; losing a vendor PCL variant off the end costs nothing by
# comparison.
ESSENTIAL_FORMATS = ["image/urf", "application/pdf", "image/jpeg"]


def _pdl_value(formats: list[str]) -> str:
    """The pdl TXT record's value, trimmed to fit if it has to be.

    Untrimmed whenever it fits, so the 52 printers already advertising keep
    byte-identical records and their clients' preference order is undisturbed.
    Only an oversized list is reordered, and only to the extent of pulling the
    essential formats to the front before filling the remaining budget in the
    printer's own order.
    """
    joined = ",".join(formats)
    budget = MAX_TXT_BYTES - len("pdl=")
    if len(joined.encode()) <= budget:
        return joined

    ordered = [f for f in ESSENTIAL_FORMATS if f in formats]
    ordered += [f for f in formats if f not in ordered]

    kept: list[str] = []
    for fmt in ordered:
        candidate = ",".join([*kept, fmt])
        if len(candidate.encode()) > budget:
            continue
        kept.append(fmt)
    # A single format longer than the budget would leave this empty, which is
    # a worse advertisement than a wrong one: a printer with no pdl at all is
    # not a printer any AirPrint client will offer.
    return ",".join(kept) if kept else "application/pdf"


def load_backend_token() -> str:
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("PRINTOPS_BACKEND_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError(f"PRINTOPS_BACKEND_TOKEN not found in {ENV_FILE}")


def api_get(token: str, path: str) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}")
    req.add_header("X-Backend-Token", token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _render_service_block(service_type: str, resource_path: str, name: str, pdl: str,
                           color: str, duplex: str, printer_id: str) -> str:
    return f"""  <service>
    <type>{service_type}</type>
    <subtype>_universal._sub.{service_type}</subtype>
    <port>631</port>
    <txt-record>txtvers=1</txt-record>
    <txt-record>qtotal=1</txt-record>
    <txt-record>rp={resource_path}</txt-record>
    <txt-record>ty={name}</txt-record>
    <txt-record>pdl={pdl}</txt-record>
    <txt-record>Color={color}</txt-record>
    <txt-record>Duplex={duplex}</txt-record>
    <txt-record>UUID={printer_id}</txt-record>
    <txt-record>note=Published by PrintOps</txt-record>
  </service>"""


def render_service_xml(printer_id: str, printer: dict, advertise_ipps: bool = False) -> str:
    """advertise_ipps (ServerSettings.advertise_ipps, Settings > Server) adds
    a second _ipps._tcp service block alongside the always-present _ipp._tcp
    one — same port (631: CUPS negotiates TLS within the same IPP/HTTP
    connection, not a separate listener), same TXT records, so AirPrint
    clients can discover the encrypted variant without losing the plaintext
    one. Purely additive — off by default, doesn't change _ipp._tcp at all."""
    caps = printer.get("capabilities") or {}
    formats = caps.get("document_formats") or DEFAULT_FORMATS
    color = "T" if caps.get("color_supported") else "F"
    duplex = "T" if caps.get("duplex_supported") else "F"

    name = escape(printer["name"])
    resource_path = escape(f"printers/printops-{printer_id}")
    pdl = escape(_pdl_value(formats))

    services = [_render_service_block("_ipp._tcp", resource_path, name, pdl, color, duplex, printer_id)]
    if advertise_ipps:
        services.append(
            _render_service_block("_ipps._tcp", resource_path, name, pdl, color, duplex, printer_id)
        )

    services_xml = "\n".join(services)
    return f"""<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{name}</name>
{services_xml}
</service-group>
"""


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: generate_avahi_service.py <printer-id>\n")
        return 1
    printer_id = sys.argv[1]

    try:
        token = load_backend_token()
        printer = api_get(token, f"/api/v1/internal/printers/{printer_id}/connection")
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        sys.stderr.write(f"ERROR: could not look up printer {printer_id}: {exc}\n")
        return 1

    # Best-effort, not fatal — a box with no ServerSettings row yet (or a
    # transient lookup failure) just falls back to the existing plaintext-
    # only advertisement rather than blocking the whole sync over it.
    advertise_ipps = False
    try:
        server_settings = api_get(token, "/api/v1/internal/server-settings")
        advertise_ipps = bool(server_settings.get("advertise_ipps"))
    except (OSError, urllib.error.URLError):
        # Falls back to advertise_ipps = False, set above — see the
        # best-effort rationale in the comment just before this try block.
        pass

    service_path = os.path.join(SERVICES_DIR, f"printops-{printer_id}.service")

    if not printer["airprint_enabled"]:
        if os.path.exists(service_path):
            os.remove(service_path)
            print(f"Removed {service_path} (airprint_enabled=false)")
        else:
            print("airprint_enabled=false, nothing to publish")
        return 0

    os.makedirs(SERVICES_DIR, exist_ok=True)
    with open(service_path, "w") as f:
        f.write(render_service_xml(printer_id, printer, advertise_ipps))
    print(f"Wrote {service_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
