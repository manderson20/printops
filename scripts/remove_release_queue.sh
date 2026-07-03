#!/usr/bin/env bash
# Removes a printer's internal direct-delivery release queue — the inverse
# of sync_release_queue.sh. Invoked automatically by the API on printer
# delete (app/printers/queue_sync.py); safe to also run manually.
#
# Usage: ./scripts/remove_release_queue.sh <printer-id>

set -uo pipefail

PRINTER_ID="${1:?Usage: remove_release_queue.sh <printer-id>}"
QUEUE_NAME="printops-release-${PRINTER_ID}"

# -x on a nonexistent queue exits non-zero — treat "already gone" as success
# rather than failing the whole delete over it.
if sudo lpadmin -x "$QUEUE_NAME" 2>/dev/null; then
  echo "Removed release queue '$QUEUE_NAME'"
else
  echo "Release queue '$QUEUE_NAME' already absent"
fi

exit 0
