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

### Installing the backend

`scripts/install_cups_backend.sh` copies `backends/printops` to
`/usr/lib/cups/backend/printops` with the ownership and mode CUPS insists on
(root-owned, `0700` — it silently refuses to run a backend that is group- or
world-writable). It is idempotent and runs from both `scripts/setup.sh` and
`infra/update-watcher/apply-update.sh`, so a deployed box picks up backend
changes on its next update.

Run it by hand after editing `backends/printops`, or the file cupsd executes
stays whatever was there before. It was installed by hand for a long time and
drifted six weeks behind this directory, still carrying spool permissions that
had already been fixed here — which is why it is scripted now.

### Steps 3 and 4 are one process, deliberately

The backend `Popen`s CUPS's `ipp` backend and forwards SIGTERM to it (see
`_install_child_signal_forwarding`). cupsd SIGTERMs a job's backend on cancel,
hold, restart and its own stuck-job timeout, and without that forwarding the
`ipp` child survives, reparents to init, and keeps retrying into the printer —
with no backoff, at several hundred connections a second. Anything spawning the
real backend must go through `_run_real_backend` for that reason; a plain
`subprocess.run` leaves no handle to kill.

The same handler also reports the job as cancelled on its way out
(`_report_signalled_exit`), because step 4 otherwise never happens for a
signalled job and its row says "printing" for the rest of time. That is not a
rare path: cupsd restarts a job whenever its queue is modified, which is to say
on every `scripts/sync_cups_queue.sh` run, and each restart runs this backend
again and creates a *second* job row. 107 rows had been stranded that way by
August 2026.

The report is best-effort and can't cover a SIGKILL or a crash, so it is only
half the fix — `app/printers/job_reconcile.py` sweeps up whatever it misses by
asking cupsd's own job record what became of each stranded job. Between them,
a `jobs` row should never be left non-terminal.

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
