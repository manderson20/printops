#!/bin/bash
# Makes CUPS keep a job's document after it prints, so ink coverage can be
# measured from it.
#
# CUPS deletes job data as soon as the job finishes unless PreserveJobFiles
# says otherwise, and its default is off. Without this the coverage loop finds
# nothing, records every job as "expired", and the feature is silently absent —
# working on whichever machine happened to have retention set, and nowhere
# else.
#
# Idempotent and conservative: it raises the value, never lowers one, and does
# not touch a configuration that is already sufficient. Restarting CUPS is left
# to the caller, because doing it unprompted on a live print server interrupts
# whatever is printing.
set -euo pipefail

CONF="${CUPSD_CONF:-/etc/cups/cupsd.conf}"
# Three days: long enough that a weekend outage does not lose a week of
# measurements, short enough that a spool of documents is not kept indefinitely.
# These are other people's documents; keeping them longer than the feature needs
# would be its own problem.
WANT_SECONDS=259200

if [ ! -f "$CONF" ]; then
  echo "No $CONF — is CUPS installed? Skipping job retention." >&2
  exit 0
fi

current="$(grep -iEm1 '^[[:space:]]*PreserveJobFiles[[:space:]]+' "$CONF" \
  | awk '{print $2}' | tr -d '\r' || true)"

case "${current,,}" in
  yes|on|true) echo "PreserveJobFiles is already unlimited — leaving it alone."; exit 0 ;;
  ''|no|off|false) current_seconds=0 ;;
  *[!0-9]*) echo "PreserveJobFiles is '$current', which is not a number — leaving it alone." >&2; exit 0 ;;
  *) current_seconds="$current" ;;
esac

if [ "$current_seconds" -ge "$WANT_SECONDS" ]; then
  echo "PreserveJobFiles is already ${current_seconds}s — leaving it alone."
  exit 0
fi

backup="${CONF}.printops-$(date +%Y%m%d%H%M%S).bak"
sudo cp "$CONF" "$backup"
echo "Backed up $CONF to $backup"

if [ -n "$current" ]; then
  sudo sed -i -E "s|^[[:space:]]*PreserveJobFiles[[:space:]]+.*|PreserveJobFiles $WANT_SECONDS|I" "$CONF"
  echo "Raised PreserveJobFiles from ${current_seconds}s to ${WANT_SECONDS}s."
else
  printf '\n# Added by PrintOps: keeps job documents long enough for ink\n# coverage measurement to read them (scripts/ensure_job_retention.sh).\nPreserveJobFiles %s\n' \
    "$WANT_SECONDS" | sudo tee -a "$CONF" >/dev/null
  echo "Set PreserveJobFiles to ${WANT_SECONDS}s."
fi

echo "Restart CUPS for this to take effect:  sudo systemctl restart cups"
