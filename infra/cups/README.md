# CUPS / IPP Proxy

CUPS runs natively on the host (not in Docker) — it's installed via `apt install cups`
as part of the IPP proxy setup, not managed by `docker-compose.yml`. A container fights
the things this needs: installing a custom backend under `/usr/lib/cups/backend/`,
Avahi/mDNS advertisement on the real LAN (not a Docker bridge), and raw socket access.

## How the proxy works

Each PrintOps-registered printer gets a CUPS queue whose device-uri is
`printops://<printer-uuid>`, not the real printer's address. CUPS invokes our custom
backend (`backends/printops` in this directory, installed to
`/usr/lib/cups/backend/printops`) for every job sent to that queue. The backend:

1. Looks up the target printer's real connection details from the PrintOps API
   (`GET /api/v1/printers/{id}`, authenticated with `PRINTOPS_BACKEND_TOKEN`).
2. Logs the job (`POST /api/v1/jobs`) before attempting delivery.
3. Delegates actual IPP delivery to CUPS's own built-in `ipp` backend, pointed at the
   real printer — reusing CUPS's already-correct IPP client rather than reimplementing
   Print-Job encoding ourselves.
4. Reports the final status back (`PATCH /api/v1/jobs/{id}`).

See `scripts/sync_cups_queue.sh` for creating a queue for a given printer.

## SNMP page/copy counter polling

`app/printers/snmp_counters.py` polls each printer's page/copy/print counters
over SNMP (see its module docstring for the per-vendor OID details) — this
requires the net-snmp CLI tools (`snmpget`/`snmpwalk`), installed the same
way as CUPS itself: `apt install snmp`. Not installed by default; only
`libsnmp-base` (the MIB/library package) tends to be present otherwise.

## Status

Phase 1 only: one manually-created queue for a single real printer, proving the
log-then-forward mechanism end-to-end. Not yet built: AirPrint/mDNS advertisement
(Avahi), policy checks (quotas/secure-release) before forwarding, and real user
attribution (currently just whatever CUPS reports, unverified) — see
`ARCHITECTURE.md` §3-4 for the full target design and phased plan.
