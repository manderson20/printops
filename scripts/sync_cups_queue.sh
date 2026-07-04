#!/usr/bin/env bash
# Creates (or updates) a CUPS queue that routes through PrintOps's custom
# backend (infra/cups/backends/printops) for a given registered printer.
#
# Usage: ./scripts/sync_cups_queue.sh <printer-id>
#
# Invoked automatically by the API (app/printers/queue_sync.py) on printer
# create/update — see that module for the non-fatal error-handling contract
# (Printer.queue_sync_error). Safe to also run manually/standalone, e.g. to
# recover after fixing whatever caused a sync failure without needing
# another edit through the UI. See also scripts/remove_cups_queue.sh.

set -euo pipefail

PRINTER_ID="${1:?Usage: sync_cups_queue.sh <printer-id>}"
API_BASE="${PRINTOPS_API_BASE:-http://localhost:8000}"
ENV_FILE="${PRINTOPS_ENV_FILE:-/home/itadmin/printops/apps/api/.env}"

TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$ENV_FILE" | cut -d= -f2)

PRINTER_JSON=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/printers/$PRINTER_ID/connection")

PRINTER_NAME=$(python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" <<<"$PRINTER_JSON")
REAL_IP=$(python3 -c "import json,sys; print(json.load(sys.stdin)['ip_address'])" <<<"$PRINTER_JSON")
REAL_PORT=$(python3 -c "import json,sys; print(json.load(sys.stdin)['port'])" <<<"$PRINTER_JSON")
REAL_SCHEME=$(python3 -c "import json,sys; print('ipps' if json.load(sys.stdin)['use_tls'] else 'ipp')" <<<"$PRINTER_JSON")
REAL_PATH=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ipp_path') or '/ipp/print')" <<<"$PRINTER_JSON")
AIRPRINT_ENABLED=$(python3 -c "import json,sys; print('true' if json.load(sys.stdin)['airprint_enabled'] else 'false')" <<<"$PRINTER_JSON")

REAL_URI="${REAL_SCHEME}://${REAL_IP}:${REAL_PORT}${REAL_PATH}"

# CUPS queue names are restricted to alnum/-/_; the printer UUID guarantees
# uniqueness, with the human name set separately via printer-info (-D).
QUEUE_NAME="printops-${PRINTER_ID}"

# Step 1: probe the REAL printer once via `-m everywhere` so CUPS builds an
# accurate driverless PPD (correct document-format/pdl advertisement, needed
# for AirPrint clients to recognize this as a usable destination) from what
# the device actually reports — not a guess.
#
# `-m everywhere` requests the device's full attribute set (all,
# media-col-database internally) — some devices can't handle that and
# hang or drop the connection outright (confirmed live: a Kyocera ECOSYS
# returns 0 bytes/service-unavailable specifically on that request, even
# though it answers smaller targeted IPP requests, like this app's own
# capability/status probes, just fine). Bounded timeout + fall back to a
# generic driverless PPD rather than let the whole sync hang the full
# Python-level timeout (queue_sync.py) with nothing to show for it —
# loses precise media-size/capability advertisement for that one queue,
# but the printer actually becomes usable instead of stuck unsynced.
if ! timeout 30 sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m everywhere -D "$PRINTER_NAME"; then
    echo "WARNING: -m everywhere failed/timed out for $REAL_URI — falling back to a generic IPP Everywhere PPD (reduced capability accuracy for this printer)." >&2
    sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m "drv:///cupsfilters.drv/pwgrast.ppd" -D "$PRINTER_NAME"
fi

# The generic-PPD fallback above can leave a newly-created queue disabled/
# rejecting jobs by default (confirmed live) — -m everywhere queues don't
# need this, but ensure both explicitly either way; enabling an
# already-enabled queue is a harmless no-op.
sudo cupsenable "$QUEUE_NAME"
sudo cupsaccept "$QUEUE_NAME"

# cupsd.conf's global ErrorPolicy is retry-job, which keeps retrying the
# SAME failed job rather than skipping to the next one — a single bad job
# (corrupt file, printer momentarily rejecting it, etc.) then jams every
# other job queued behind it on this printer until someone notices and
# cancels it manually. abort-job cancels just the failing job and lets the
# queue keep moving; app/printers/queue_sync.py's custom backend already
# records the failure on the Job row (visible on the Jobs page) regardless
# of this policy, so nothing is lost by not stopping to retry.
sudo lpadmin -p "$QUEUE_NAME" -o printer-error-policy=abort-job

# Step 2: repoint device-uri only, back to our backend, so jobs still route
# through PrintOps for logging + forwarding. The PPD/capabilities CUPS just
# derived from the real printer in step 1 are kept.
sudo lpadmin -p "$QUEUE_NAME" -v "printops://${PRINTER_ID}"

# printer-is-shared is always true — it's what allows ANY network client
# (discovered via mDNS or explicitly configured, e.g. an MDM-pushed printer
# profile) to reach the queue at all; CUPS refuses network job submission
# entirely when a queue isn't shared, regardless of how the client found it.
# airprint_enabled below controls *discovery* only, not this.
sudo lpadmin -p "$QUEUE_NAME" -o printer-is-shared=true -E

# mDNS/AirPrint *discoverability* is controlled per-printer via PrintOps.
# Off (airprint_enabled=false) means the queue won't show up in automatic
# "Add Printer" pickers, but still accepts jobs from explicitly-configured
# clients (e.g. MDM) since sharing itself (above) is unaffected by this.
# cupsd's own DNS-SD publishing doesn't work on this box (confirmed via
# debug logging — see infra/cups/README.md), so we publish the AirPrint
# advertisement ourselves via a static Avahi service file instead.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo python3 "${SCRIPT_DIR}/../infra/cups/generate_avahi_service.py" "$PRINTER_ID"

echo "Queue '$QUEUE_NAME' -> printops://${PRINTER_ID} (${PRINTER_NAME}), airprint_enabled=${AIRPRINT_ENABLED}"
