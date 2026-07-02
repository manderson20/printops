#!/usr/bin/env python3
"""Generates (or removes) a static Avahi service file for a printer's
AirPrint/Bonjour advertisement.

This CUPS build's own dnssd publishing doesn't work on this box — confirmed
via debug-level logging showing zero avahi/dnssd activity despite correct
config (Browsing Yes, shared queue, AppArmor net_admin capability granted).
Avahi's static service-file mechanism (this script) is the workaround: drop
an XML file in /etc/avahi/services/ and avahi-daemon picks it up itself
(inotify-watched, no restart needed). See infra/cups/README.md.

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


def render_service_xml(printer_id: str, printer: dict) -> str:
    caps = printer.get("capabilities") or {}
    formats = caps.get("document_formats") or DEFAULT_FORMATS
    color = "T" if caps.get("color_supported") else "F"
    duplex = "T" if caps.get("duplex_supported") else "F"

    name = escape(printer["name"])
    resource_path = escape(f"printers/printops-{printer_id}")
    pdl = escape(",".join(formats))

    return f"""<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{name}</name>
  <service>
    <type>_ipp._tcp</type>
    <subtype>_universal._sub._ipp._tcp</subtype>
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
  </service>
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
        f.write(render_service_xml(printer_id, printer))
    print(f"Wrote {service_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
