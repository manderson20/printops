#!/usr/bin/env bash
# Cancels every job queued on a printer's CUPS queue — including jobs
# PrintOps has no DB row for yet (a Job row is only created once cupsd
# actually starts running our backend for it; anything still waiting behind
# a jammed/errored job is invisible to the app until then).
#
# Invoked by the API (app/printers/job_control.py) via the admin "Purge
# Queue" action. Safe to also run manually.
#
# Usage: ./scripts/purge_cups_queue.sh <printer-id>

set -uo pipefail

PRINTER_ID="${1:?Usage: purge_cups_queue.sh <printer-id>}"
QUEUE_NAME="printops-${PRINTER_ID}"

# -a -p <queue> cancels all jobs on that queue. Exits non-zero when the
# queue has no jobs to cancel — treat that as success, same tolerant style
# as remove_cups_queue.sh/cancel_cups_job.sh.
if sudo cancel -a -p "$QUEUE_NAME" 2>/dev/null; then
  echo "Purged all jobs on CUPS queue '$QUEUE_NAME'"
else
  echo "CUPS queue '$QUEUE_NAME' had no jobs to purge"
fi

exit 0
