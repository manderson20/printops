#!/usr/bin/env bash
# Pauses a printer's CUPS queues so cupsd stops sending to it.
#
# Used when the printer stops answering while cupsd is part-way through
# sending it a job (app/printers/crash_guard.py). Left running, CUPS retries
# that job for as long as it takes, so a printer that crashed on it and was
# power-cycled receives the same job again moments after it boots. On
# 2026-09-11 that kept an office LaserJet crashing on one PDF for three days.
# Paused, the printer comes back idle, and queue recovery
# (scripts/resume_cups_queue.sh) starts the queue again once PrintOps sees it
# answer.
#
# Pausing cancels nothing: the job being sent goes back to waiting, and the
# queue keeps accepting new jobs (confirmed against CUPS 2.4.16).
#
# Usage: ./scripts/pause_cups_queue.sh <printer-id>

set -uo pipefail

PRINTER_ID="${1:?Usage: pause_cups_queue.sh <printer-id>}"
REASON="PrintOps paused this queue: the printer stopped answering while a job was being sent to it."

paused_any=0

for QUEUE_NAME in "printops-${PRINTER_ID}" "printops-release-${PRINTER_ID}"; do
    # Same tolerance as resume_cups_queue.sh: a virtual Follow-Me printer has
    # no release queue.
    if ! LC_ALL=C lpstat -p "$QUEUE_NAME" >/dev/null 2>&1; then
        continue
    fi
    # The exit status is the API's whole verdict (queue_recovery.pause_queue),
    # so a refusal is reported rather than printed over.
    if ! command_output=$(sudo cupsdisable -r "$REASON" "$QUEUE_NAME" 2>&1); then
        echo "cupsdisable failed for '$QUEUE_NAME': ${command_output:-no output}" >&2
        exit 1
    fi
    echo "Paused CUPS queue '$QUEUE_NAME'"
    paused_any=1
done

if [ "$paused_any" -eq 0 ]; then
    echo "No CUPS queues found for printer '$PRINTER_ID'" >&2
    exit 1
fi

exit 0
