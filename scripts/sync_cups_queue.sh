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

# CUPS queue names are restricted to alnum/-/_; the printer UUID guarantees
# uniqueness, with the human name set separately via printer-info (-D).
QUEUE_NAME="printops-${PRINTER_ID}"

# -m raw: no CUPS-side filtering/rasterization. The document is passed through
# to our backend as-submitted; the real printer negotiates format itself when
# we forward via CUPS's own `ipp` backend. Matches how the capability probe
# already confirmed this device's supported document formats.
sudo lpadmin -p "$QUEUE_NAME" \
  -v "printops://${PRINTER_ID}" \
  -m raw \
  -D "$PRINTER_NAME" \
  -E

echo "Queue '$QUEUE_NAME' -> printops://${PRINTER_ID} (${PRINTER_NAME})"
