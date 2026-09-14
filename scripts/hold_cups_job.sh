#!/usr/bin/env bash
# Holds or releases one CUPS job by its (global) CUPS job ID.
#
# Invoked by the API (app/printers/job_control.py): "hold" when a printer has
# stopped answering twice while being sent the same job
# (app/printers/crash_guard.py), "resume" when an admin releases that job from
# the printer's page. Holding works on a job cupsd is printing as well as one
# that is waiting, and the queue moves on to the next job either way
# (confirmed against CUPS 2.4.16).
#
# Usage: ./scripts/hold_cups_job.sh <cups-job-id> <hold|resume>

set -uo pipefail

CUPS_JOB_ID="${1:?Usage: hold_cups_job.sh <cups-job-id> <hold|resume>}"
ACTION="${2:?Usage: hold_cups_job.sh <cups-job-id> <hold|resume>}"

case "$CUPS_JOB_ID" in
  ''|*[!0-9]*) echo "Job id must be a number, got '$CUPS_JOB_ID'" >&2; exit 2 ;;
esac
case "$ACTION" in
  hold|resume) ;;
  *) echo "Action must be hold or resume, got '$ACTION'" >&2; exit 2 ;;
esac

# Not treated as success on failure, unlike cancel_cups_job.sh: a hold that did
# not happen leaves the printer being sent the job it keeps failing on.
if ! output=$(sudo lp -i "$CUPS_JOB_ID" -H "$ACTION" 2>&1); then
  echo "${output:-lp exited non-zero}" >&2
  exit 1
fi

echo "CUPS job $CUPS_JOB_ID: $ACTION"
