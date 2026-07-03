#!/usr/bin/env bash
# Cancels a single in-flight CUPS job by its (global) CUPS job ID — the same
# ID stored on Job.cups_job_id (see infra/cups/backends/printops, which is
# handed job_id as argv[1] by cupsd).
#
# Invoked by the API (app/printers/job_control.py) when an admin cancels a
# job from the Jobs page. Safe to also run manually.
#
# Usage: ./scripts/cancel_cups_job.sh <cups-job-id>

set -uo pipefail

CUPS_JOB_ID="${1:?Usage: cancel_cups_job.sh <cups-job-id>}"

# `cancel` on a job that's already finished/gone exits non-zero — treat that
# as success too, same as remove_cups_queue.sh's "-x on nonexistent queue"
# handling, since the admin's actual goal (this job is no longer active) is
# already satisfied either way.
if sudo cancel "$CUPS_JOB_ID" 2>/dev/null; then
  echo "Cancelled CUPS job $CUPS_JOB_ID"
else
  echo "CUPS job $CUPS_JOB_ID already gone"
fi

exit 0
