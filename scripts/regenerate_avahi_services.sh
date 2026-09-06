#!/usr/bin/env bash
# Rewrites every printer's Avahi service file to match its current
# airprint_enabled flag.
#
# Usage: sudo ./scripts/regenerate_avahi_services.sh
#
# Normally unnecessary: scripts/sync_cups_queue.sh runs the generator for one
# printer on every queue sync, which is enough to keep a running estate
# correct. This exists for the case where the *flag* changed underneath the
# queues without any sync happening — which is exactly what migration 0079
# does when it records that the existing fleet was already discoverable (#110).
#
# Order matters on that upgrade, and getting it wrong is an outage:
#
#   1. alembic upgrade head        — the flags become true
#   2. this script                 — the service files appear, and are now
#                                    advertised *alongside* cupsd's own
#   3. disable BrowseLocalProtocols dnssd in /etc/cups/cupsd.conf
#   4. sudo systemctl restart cups — cupsd's blanket advertisement stops
#
# Run in that order there is never a moment when a printer is unadvertised;
# steps 2-4 briefly double-advertise instead, which avahi handles by
# disambiguating the names and which nobody notices. Do step 4 before step 2
# and every printer on the estate vanishes from Add Printer pickers until
# something happens to resync it.
#
# Idempotent, and safe to run at any time: the generator writes a file for a
# printer whose flag is true and removes it for one whose flag is false, so
# this converges on whatever the database currently says.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_BASE="${PRINTOPS_API_BASE:-http://localhost:8000}"
ENV_FILE="${PRINTOPS_ENV_FILE:-/home/itadmin/printops/apps/api/.env}"

TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$ENV_FILE" | cut -d= -f2)

# Archived printers are deliberately absent from this endpoint — their queues
# and service files were removed when they were archived, and republishing one
# would advertise a printer nobody expects to exist.
PRINTER_IDS=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/printers/ids" \
    | python3 -c "import json,sys; print('\n'.join(p['id'] for p in json.load(sys.stdin)))")

if [ -z "$PRINTER_IDS" ]; then
    echo "No printers returned by $API_BASE — nothing to do." >&2
    exit 0
fi

# Counted rather than assumed: this runs as part of an upgrade whose whole
# risk is publishing the wrong number of printers, so it says what it did.
total=0
failed=0
while read -r printer_id; do
    [ -z "$printer_id" ] && continue
    total=$((total + 1))
    # One printer failing (a name the generator chokes on, a transient API
    # error) must not abandon the rest half-published — that would leave the
    # estate in exactly the split state this script exists to avoid.
    if ! python3 "${SCRIPT_DIR}/../infra/cups/generate_avahi_service.py" "$printer_id"; then
        echo "WARNING: could not regenerate the advertisement for $printer_id" >&2
        failed=$((failed + 1))
    fi
done <<<"$PRINTER_IDS"

published=$(find /etc/avahi/services -name 'printops-*.service' 2>/dev/null | wc -l)
echo "Processed $total printers ($failed failed); $published are now advertised by PrintOps."

if [ "$failed" -gt 0 ]; then
    exit 1
fi
