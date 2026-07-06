#!/usr/bin/env bash
# Creates (or updates) a printer's *internal* direct-delivery CUPS queue —
# used only by app/printers/release.py to actually deliver a held job once
# it's released, via `lp -d printops-release-<id>`. Unlike the client-facing
# queue (scripts/sync_cups_queue.sh), this one's device-uri stays pointed at
# the real printer — it never routes through our backend, is never shared,
# and is never AirPrint-advertised, since no client should ever be able to
# target it directly.
#
# Usage: ./scripts/sync_release_queue.sh <printer-id>
#
# Invoked automatically alongside the client-facing queue sync
# (app/printers/queue_sync.py:sync_queue) for every printer, regardless of
# whether release is currently enabled — cheap to keep around, and avoids a
# separate create/remove lifecycle tied to toggling release_required.

set -euo pipefail

PRINTER_ID="${1:?Usage: sync_release_queue.sh <printer-id>}"
API_BASE="${PRINTOPS_API_BASE:-http://localhost:8000}"
ENV_FILE="${PRINTOPS_ENV_FILE:-/home/itadmin/printops/apps/api/.env}"

TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$ENV_FILE" | cut -d= -f2)

PRINTER_JSON=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/printers/$PRINTER_ID/connection")

PRINTER_NAME=$(python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" <<<"$PRINTER_JSON")
REAL_IP=$(python3 -c "import json,sys; print(json.load(sys.stdin)['ip_address'])" <<<"$PRINTER_JSON")
REAL_PORT=$(python3 -c "import json,sys; print(json.load(sys.stdin)['port'])" <<<"$PRINTER_JSON")
REAL_SCHEME=$(python3 -c "import json,sys; print('ipps' if json.load(sys.stdin)['use_tls'] else 'ipp')" <<<"$PRINTER_JSON")
REAL_PATH=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ipp_path') or '/ipp/print')" <<<"$PRINTER_JSON")

REAL_URI="${REAL_SCHEME}://${REAL_IP}:${REAL_PORT}${REAL_PATH}"
QUEUE_NAME="printops-release-${PRINTER_ID}"

# -m everywhere builds an accurate driverless PPD from what the device
# actually reports, same as the client-facing queue — this queue talks to
# the real printer directly, so device-uri is left pointed there (no
# repoint-to-printops:// step, unlike sync_cups_queue.sh).
#
# Same bounded-timeout + generic-PPD fallback as sync_cups_queue.sh — see
# that script's comment for why (confirmed live: a Kyocera ECOSYS can't
# handle -m everywhere's full attribute probe on either queue).
if ! timeout 30 sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m everywhere -D "${PRINTER_NAME} (internal release queue)"; then
    echo "WARNING: -m everywhere failed/timed out for $REAL_URI — falling back to a generic IPP Everywhere PPD (reduced capability accuracy for this release queue)." >&2
    sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m "drv:///cupsfilters.drv/pwgrast.ppd" -D "${PRINTER_NAME} (internal release queue)"
fi

# Deliberately NOT shared and NOT AirPrint-advertised — this queue only
# ever receives jobs from app/printers/release.py's own `lp -d` call on
# this same host, never from a network client.
sudo lpadmin -p "$QUEUE_NAME" -o printer-is-shared=false -E

# Same as the client-facing queue — the generic-PPD fallback can leave a
# newly-created queue disabled/rejecting by default; ensure both
# explicitly, harmless no-op if already enabled/accepting.
sudo cupsenable "$QUEUE_NAME"
sudo cupsaccept "$QUEUE_NAME"

# Same as the client-facing queue — force color-capable printers to default
# to color rather than whatever driverless-PPD generation happened to land
# on (confirmed live: 4 color copiers on this box had a stored
# print-color-mode=monochrome default despite being genuine color devices).
# See scripts/sync_cups_queue.sh's matching block for the full reasoning.
COLOR_SUPPORTED=$(ipptool -X "ipp://localhost/printers/$QUEUE_NAME" /dev/stdin <<IPPTOOL_EOF 2>/dev/null | grep -A1 "<key>color-supported</key>" | grep -c "<true" || true
{
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri ipp://localhost/printers/$QUEUE_NAME
    ATTR keyword requested-attributes color-supported
}
IPPTOOL_EOF
)
if [ "$COLOR_SUPPORTED" -ge 1 ]; then
    sudo lpadmin -p "$QUEUE_NAME" -o print-color-mode-default=color
fi

# Same as the client-facing queue — abort just the failing job instead of
# cupsd's default retry-job, which would otherwise jam every other
# released job behind it on this same internal delivery queue. See that
# script's comment for the full reasoning.
sudo lpadmin -p "$QUEUE_NAME" -o printer-error-policy=abort-job

echo "Release queue '$QUEUE_NAME' -> ${REAL_URI} (${PRINTER_NAME})"
