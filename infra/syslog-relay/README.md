# Syslog collector

Listens for UDP syslog messages printers/MFPs send when their event
notification / syslog export setting is pointed at this box, and posts them
to printops-api so they show up per-device on the printer detail page (and
fleet-wide on the **Syslog** page) — a place to check for errors when
diagnosing a jam, an offline printer, or anything else worth investigating
beyond what SNMP counters or IPP status already show.

## Why a separate service, not part of printops-api?

Same reasoning as `infra/ldap-relay/`: UDP 514 is a privileged port, and
granting `CAP_NET_BIND_SERVICE` to one small, single-purpose process is a
narrower blast radius than granting it to the whole API service. Unlike the
LDAP relay, this one doesn't need Twisted/ldaptor — UDP syslog has no
handshake or session state, so plain `asyncio.DatagramProtocol` is enough;
`server.py` parses each datagram (RFC 3164, the older BSD format most real
MFP firmware actually speaks, with an RFC 5424 fallback) and batches parsed
messages (up to 200, or every 2s, whichever comes first — a jam can burst
many lines at once) into a POST to printops-api's internal, backend-token
gated `POST /api/v1/internal/syslog/events`. Matching a message's source IP
to a `Printer`, applying the configured severity floor, and persisting it
all happens there (`app/syslog/service.py`), not in this process — same
split as the LDAP relay's "protocol translator only" design.

## Install (one-time, on the box running printops-api)

```bash
cd infra/syslog-relay
uv sync
sudo cp printops-syslog-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now printops-syslog-relay.service
```

The relay listens regardless of settings (same as the LDAP relay) — nothing
is actually stored until an admin turns collection on in
**Settings → Syslog**, which also sets the noise-floor severity and
retention period.

## Configuring a printer

On the printer/MFP's own admin web UI (location and terminology vary by
vendor — look for "syslog", "event notification", or "SNMP trap/syslog
destination"):
- **Server**: this box's hostname/IP.
- **Port**: 514 (most vendors don't let you choose a different one — if
  yours does, set `PRINTOPS_SYSLOG_PORT` in the systemd unit to match and
  restart the service).
- **Protocol**: UDP, plain (no TLS support here — same trust model as this
  system's existing SNMP polling and optional-TLS IPP).

## Manual verification

There's no automated protocol-level test (a separate process from
printops-api's pytest suite, same as the LDAP relay) — the parsing logic in
`server.py` can be exercised directly:

```bash
python3 -c "
from server import parse_syslog_message
from datetime import datetime, UTC
print(parse_syslog_message('<14>Jul 10 09:15:22 CANON-MF644C ScanEngine: Paper jam detected in tray 2', datetime.now(UTC), '10.0.1.42'))
"
```

Or send a real UDP packet with `logger` (from a machine other than this
one, to also exercise the network path):

```bash
logger -n <this-box-ip> -P 514 -d "Test message from logger"
```

Then check `GET /api/v1/syslog` (or the Syslog page in the web app) for the
event — it'll show up unmatched (no printer) unless `logger`'s source IP
happens to match a registered `Printer.ip_address`.

## Checking it's running

```bash
systemctl status printops-syslog-relay.service
journalctl -u printops-syslog-relay.service -n 50
```
