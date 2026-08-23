#!/usr/bin/env bash
# Re-enables a printer's CUPS queues after cupsd stopped them.
#
# cupsd stops a queue by itself when a backend exits with CUPS_BACKEND_STOP
# (4) — and it does so regardless of the queue's ErrorPolicy, so the
# `abort-job` policy sync_cups_queue.sh sets is no protection against it.
# Nothing then ever starts the queue again. On 2026-08-21 the ES Veronica
# Copier was taken away for a Service Call 0206; the backend hit a broken
# pipe mid Send-Document, cupsd stopped the queue, and when the copier came
# back serviced the queue stayed stopped for 31 hours with 19 teachers' jobs
# behind it. The queue was still "Accepting Yes" the whole time, so jobs
# kept arriving and nothing ever came out.
#
# Both queues are resumed: the client-facing one and the internal release
# queue (app/printers/release.py), which delivers to the same device over
# IPP and can be stopped by exactly the same failure.
#
# A stopped queue is always an accident here — PrintOps has no "pause this
# printer" feature. Retiring a printer archives it, which tears the queue
# down entirely (app/routers/printers.py:archive_printer), so there is no
# deliberately-stopped state for this to trample.
#
# Invoked by the API (app/printers/queue_recovery.py). Safe to run manually.
#
# Usage: ./scripts/resume_cups_queue.sh <printer-id>

set -uo pipefail

PRINTER_ID="${1:?Usage: resume_cups_queue.sh <printer-id>}"

resumed_any=0

for QUEUE_NAME in "printops-${PRINTER_ID}" "printops-release-${PRINTER_ID}"; do
    # A missing queue is not an error: the release queue doesn't exist for a
    # virtual Follow-Me printer, and a printer whose queue was never synced
    # has neither. Same tolerant style as purge_cups_queue.sh.
    if ! LC_ALL=C lpstat -p "$QUEUE_NAME" >/dev/null 2>&1; then
        continue
    fi

    # cupsaccept as well as cupsenable: a queue that is rejecting jobs is
    # just as dead as one that is stopped, and both are set together by
    # sync_cups_queue.sh for the same reason. Enabling an already-enabled
    # queue is a harmless no-op, so this needs no "is it stopped?" check.
    #
    # Both are checked, because this script's exit code is the whole verdict
    # the API gets (app/printers/queue_recovery.py:resume_queue). There is no
    # `set -e` here, and these used to discard stderr and ignore the status:
    # if sudo or cupsd refused, the script still printed "Resumed" and exited
    # 0, so PrintOps would report the queue restarted, clear the diagnosis and
    # stop asking — leaving jobs piling up behind a queue that never started.
    # That is the 31-hour failure at the top of this file, reintroduced one
    # level further up.
    for cups_command in cupsenable cupsaccept; do
        if ! command_output=$(sudo "$cups_command" "$QUEUE_NAME" 2>&1); then
            echo "$cups_command failed for '$QUEUE_NAME': ${command_output:-no output}" >&2
            exit 1
        fi
    done
    echo "Resumed CUPS queue '$QUEUE_NAME'"
    resumed_any=1
done

if [ "$resumed_any" -eq 0 ]; then
    echo "No CUPS queues found for printer '$PRINTER_ID'" >&2
    exit 1
fi

exit 0
