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

## Queue discovery (mDNS / AirPrint)

Two separate things advertise printers over DNS-SD on this box, and confusing
them is what made `airprint_enabled` a lie for 53 printers (#110).

**cupsd advertises every *shared* queue.** Every PrintOps queue is shared —
`scripts/sync_cups_queue.sh` sets `printer-is-shared=true` unconditionally,
because CUPS refuses network job submission to a queue that isn't shared. So
when cupsd is publishing, it publishes all of them, and there is no per-queue
switch in CUPS to map a PrintOps flag onto.

**PrintOps advertises the queues an admin has opted in.**
`generate_avahi_service.py` writes one static Avahi service file per printer
with `airprint_enabled = true`, and removes it when the flag goes false.
avahi-daemon picks the files up by inotify — no restart needed.

Both at once means the second is invisible: cupsd's blanket advertisement
covers every queue whatever PrintOps does or doesn't publish. The Printers page
read "Queue discovery: Hidden" for all 53 printers while a browse of
`_ipp._tcp` returned 108 records, all of them PrintOps queues.

So **cupsd's DNS-SD publishing must stay off** for the per-printer toggle to
mean anything:

```
# /etc/cups/cupsd.conf
Browsing Yes
BrowseLocalProtocols none   # NOT commented out — see below. #110
```

**Set it to `none`; do not comment it out.** `BrowseLocalProtocols` defaults to
`dnssd` when absent, so deleting or commenting the line leaves cupsd publishing
exactly as before. This was got wrong once on this box: the line was commented
out, cups restarted, and all 108 cupsd records were still there afterwards —
the only sign being a browse still returning 212 records instead of the
expected 108.

`Browsing Yes` alongside it is harmless: it governs whether cupsd browses at
all, and with local protocols set to `none` it publishes nothing. Re-adding
`dnssd` silently restores the old behaviour — everything discoverable, the
toggle inert again, and no error anywhere to say so.

Check it with a browse rather than by reading the config, since the failure
mode here is a setting that looks disabled and isn't:

```
avahi-browse -rpt _ipp._tcp | grep -c '^='                       # total
avahi-browse -rpt _ipp._tcp | grep -c 'Published by PrintOps'    # ours
```

Those two numbers should be equal. If the first is larger, cupsd is still
publishing.

### Upgrading an estate that was relying on cupsd's advertisement

Order matters here and getting it wrong is an outage, because until the
service files exist cupsd is the only thing publishing anything:

```
alembic upgrade head                        # 1. flags become true (0079)
sudo ./scripts/regenerate_avahi_services.sh # 2. service files appear
#    3. disable BrowseLocalProtocols dnssd in /etc/cups/cupsd.conf
sudo systemctl restart cups                 # 4. cupsd stops advertising
```

In that order no printer is ever unadvertised — steps 2-4 briefly
double-advertise instead, which avahi handles by disambiguating the names and
which nobody notices. Do step 4 before step 2 and every printer on the estate
disappears from Add Printer pickers until something happens to resync it.

`regenerate_avahi_services.sh` is idempotent and converges on whatever the
database currently says, so it is safe to re-run at any point.

### A note on the previous explanation

Earlier comments in `sync_cups_queue.sh` and this file said the static-file
mechanism existed because *cupsd's own DNS-SD publishing did not work on this
box*, confirmed at the time via debug logging showing no avahi activity. That
is no longer true — cupsd publishes fine here now, and the records it produces
carry its signature (`rp=printers/printops-<uuid>`, an `adminurl` pointing at
this server). The static files are still the right mechanism, but for a
different reason: cupsd's publishing is all-or-nothing, and this needs to be
per-printer.
