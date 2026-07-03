#!/bin/bash
# Polls this server's own API for a due scheduled update; once its
# scheduled time arrives, runs apply-update.sh and reports the result back.
# Installed as a systemd timer (printops-update-watcher.timer, every
# minute) — see app/models/update_schedule.py and app/routers/updates.py
# for why this lives outside the API process instead of the API just
# doing this in a request handler: restarting printops-api.service is
# part of the update, which would kill the very request reporting it.
set -euo pipefail

REPO_DIR="/home/itadmin/printops"
API_BASE="http://127.0.0.1:8000"
BACKEND_TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$REPO_DIR/apps/api/.env" | cut -d= -f2-)

RESPONSE=$(curl -sf -H "X-Backend-Token: $BACKEND_TOKEN" "$API_BASE/api/v1/updates/status") || exit 0
PENDING=$(echo "$RESPONSE" | jq -r '.pending')
[ "$PENDING" = "null" ] && exit 0

STATUS=$(echo "$RESPONSE" | jq -r '.pending.status')
SCHEDULED_AT=$(echo "$RESPONSE" | jq -r '.pending.scheduled_at')
[ "$STATUS" != "pending" ] && exit 0

SCHEDULED_EPOCH=$(date -d "$SCHEDULED_AT" +%s 2>/dev/null) || exit 0
NOW_EPOCH=$(date +%s)
[ "$NOW_EPOCH" -lt "$SCHEDULED_EPOCH" ] && exit 0

logger "PrintOps update-watcher: scheduled update time reached, starting"
curl -sf -X POST -H "X-Backend-Token: $BACKEND_TOKEN" -H "Content-Type: application/json" \
  -d '{"status":"in_progress"}' "$API_BASE/api/v1/updates/complete" || true

LOG_FILE=$(mktemp)
if bash "$REPO_DIR/infra/update-watcher/apply-update.sh" > "$LOG_FILE" 2>&1; then
  FINAL_STATUS=completed
  logger "PrintOps update-watcher: update completed successfully"
else
  FINAL_STATUS=failed
  logger "PrintOps update-watcher: update FAILED — check $LOG_FILE"
fi

# The DB column has no fixed cap, but keeping the reported log bounded
# avoids ever shipping a pathological amount of build/install output back
# over HTTP into a text column shown directly in the admin UI.
LOG_JSON=$(jq -Rs --arg status "$FINAL_STATUS" '{status: $status, log: .}' < <(tail -c 4000 "$LOG_FILE"))
curl -sf -X POST -H "X-Backend-Token: $BACKEND_TOKEN" -H "Content-Type: application/json" \
  -d "$LOG_JSON" "$API_BASE/api/v1/updates/complete" || true

if [ "$FINAL_STATUS" = "failed" ]; then
  # Keep the FULL log on disk for a failed run, even though in practice
  # it's usually well under the 4000-byte cap sent to the DB above — a
  # hedge for a run whose real output exceeds that. /var/log survives
  # reboots; /tmp may not depending on the distro's tmp-on-tmpfs setup.
  FAIL_LOG_DIR="/var/log/printops-updates"
  sudo mkdir -p "$FAIL_LOG_DIR"
  sudo cp "$LOG_FILE" "$FAIL_LOG_DIR/update-failed-$(date -u +%Y%m%dT%H%M%SZ).log"
  # Retention: keep only the 20 most recent failure logs.
  sudo bash -c "ls -1t '$FAIL_LOG_DIR'/update-failed-*.log 2>/dev/null | tail -n +21 | xargs -r rm -f"
fi
rm -f "$LOG_FILE"
