#!/usr/bin/env bash
# Removes a printer's CUPS queue and Avahi AirPrint advertisement — the
# inverse of sync_cups_queue.sh. Invoked automatically by the API on printer
# delete (app/printers/queue_sync.py); safe to also run manually.
#
# Usage: ./scripts/remove_cups_queue.sh <printer-id>

set -uo pipefail

PRINTER_ID="${1:?Usage: remove_cups_queue.sh <printer-id>}"
QUEUE_NAME="printops-${PRINTER_ID}"

# -x on a nonexistent queue exits non-zero — treat "already gone" as success
# rather than failing the whole delete over it.
if sudo lpadmin -x "$QUEUE_NAME" 2>/dev/null; then
  echo "Removed CUPS queue '$QUEUE_NAME'"
else
  echo "CUPS queue '$QUEUE_NAME' already absent"
fi

# Matches the naming generate_avahi_service.py uses.
SERVICE_FILE="/etc/avahi/services/${QUEUE_NAME}.service"
if [ -e "$SERVICE_FILE" ]; then
  sudo rm -f "$SERVICE_FILE"
  echo "Removed Avahi service file $SERVICE_FILE"
fi

exit 0
