#!/usr/bin/env bash
# Changes the CUPS priority of one queued job, by its (global) CUPS job ID —
# the same ID stored on Job.cups_job_id.
#
# Invoked by the API (app/printers/job_control.py) when someone uses "Let
# others go first" on the Queue page. CUPS prints in priority order (higher
# first, 1-100, default 50) and breaks ties by submission time, so dropping a
# job to 1 puts it behind everything else waiting, and putting it back to 50
# returns it to its original place in the line.
#
# Only a pending job can be reprioritised — cupsd rejects the change once the
# job is printing, which is the behaviour we want and not something this
# script needs to second-guess. The API checks the state first anyway so the
# person gets a sentence rather than an lp error.
#
# Usage: ./scripts/set_cups_job_priority.sh <cups-job-id> <priority>

set -uo pipefail

CUPS_JOB_ID="${1:?Usage: set_cups_job_priority.sh <cups-job-id> <priority>}"
PRIORITY="${2:?Usage: set_cups_job_priority.sh <cups-job-id> <priority>}"

case "$CUPS_JOB_ID" in
  ''|*[!0-9]*) echo "Job id must be a number, got '$CUPS_JOB_ID'" >&2; exit 2 ;;
esac
case "$PRIORITY" in
  ''|*[!0-9]*) echo "Priority must be a number, got '$PRIORITY'" >&2; exit 2 ;;
esac
if [ "$PRIORITY" -lt 1 ] || [ "$PRIORITY" -gt 100 ]; then
  echo "Priority must be between 1 and 100, got $PRIORITY" >&2
  exit 2
fi

# Unlike cancel_cups_job.sh, a failure here is NOT treated as success: if the
# priority didn't change, the job has not moved, and telling someone their
# large job is now waiting its turn when it is still at the head of the queue
# is worse than telling them it didn't work.
if ! output=$(sudo lp -i "$CUPS_JOB_ID" -q "$PRIORITY" 2>&1); then
  echo "${output:-lp exited non-zero}" >&2
  exit 1
fi

echo "CUPS job $CUPS_JOB_ID priority set to $PRIORITY"
