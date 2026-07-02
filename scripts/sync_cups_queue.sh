#!/usr/bin/env bash
# Creates (or updates) a CUPS queue that routes through PrintOps's custom
# backend (infra/cups/backends/printops) for a given registered printer.
#
# Usage: ./scripts/sync_cups_queue.sh <printer-id>
#
# Phase 1: manual/one-at-a-time. Auto-syncing every printer in the `printers`
# table into CUPS automatically is later work (see ARCHITECTURE.md IPP proxy
# phases), not built yet.

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
sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m everywhere -D "$PRINTER_NAME"

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
