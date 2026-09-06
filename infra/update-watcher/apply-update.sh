#!/bin/bash
# Does the actual work of a software update: pull, migrate, rebuild, restart.
# Never invoked directly by the API (restarting printops-api.service mid
# request would kill the request reporting its own completion) — only by
# update-watcher.sh, once the scheduled time in update_schedule has arrived.
# See app/models/update_schedule.py and app/routers/updates.py.
set -euo pipefail

REPO_DIR="/home/itadmin/printops"
cd "$REPO_DIR"

echo "== Checking for uncommitted local changes =="
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: uncommitted local changes present in $REPO_DIR — refusing to update." >&2
  echo "Commit or stash them, then re-schedule." >&2
  exit 1
fi

echo "== Pulling latest from origin/main =="
git fetch origin main
# --ff-only, not a merge/rebase: if this box's main has diverged from
# origin/main (shouldn't happen — this working tree is also the live
# deployment, nothing else should be committing to it), fail loudly rather
# than create a merge commit or silently rewrite history on a box that's
# also serving live traffic.
git merge --ff-only origin/main

echo "== Syncing API dependencies =="
cd "$REPO_DIR/apps/api"
source .venv/bin/activate
# --extra dev: this box doubles as the dev/test environment (pytest/ruff
# are how changes get verified here) as well as the deployment target —
# a plain `uv sync` reconciles the venv to the base dependency set only
# and silently removes them, discovered when a real scheduled update did
# exactly that mid-session.
uv sync --quiet --extra dev

# Removing the service from infra/docker-compose.yml does not stop a container
# that is already running, and this one was published on 0.0.0.0:6379 with no
# password (0.75.8). An install that upgrades without this keeps the open port
# indefinitely, so the removal has to happen on the upgrade path rather than in
# a compose file nobody re-applies.
#
# Idempotent and deliberately narrow: one container, by exact name, ignored if
# absent. This is not a general "prune what compose no longer declares" step —
# that would be a much larger promise than an updater should make.
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'printops-redis-1'; then
    echo "== Removing the unused Redis container (0.75.8) =="
    docker rm -f printops-redis-1 >/dev/null 2>&1 || \
        echo "WARNING: could not remove printops-redis-1 — it may still be listening on 6379" >&2
fi

echo "== Running database migrations =="
timeout 120 alembic upgrade head

echo "== Installing web dependencies and building =="
cd "$REPO_DIR/apps/web"
pnpm install --frozen-lockfile
timeout 300 pnpm build

echo "== Ensuring held-job spool permissions/group membership =="
# scripts/setup.sh only runs on a fresh install; already-deployed instances
# get here instead, and need the same root:lp / 2770 migration re-applied
# on every update in case it drifted or was never run — otherwise the
# CUPS backend's own 0o2770 enforcement (infra/cups/backends/printops)
# locks the API out of releasing/purging held jobs.
# Same reasoning as the spool-group call below: an already-deployed box
# never re-runs setup.sh, so without this the backend in /usr/lib/cups
# keeps whatever version was copied there by hand. It drifted six weeks
# behind the repo once already (see scripts/install_cups_backend.sh).
"$REPO_DIR/scripts/install_cups_backend.sh"

"$REPO_DIR/scripts/ensure_held_spool_group.sh"

echo "== Restarting printops-api.service =="
sudo systemctl restart printops-api.service
API_OK=false
for _ in $(seq 1 20); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/healthz 2>/dev/null || \
     curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ | grep -q '^[24]'; then
    API_OK=true
    break
  fi
  sleep 1
done
if [ "$API_OK" != "true" ]; then
  echo "ERROR: printops-api.service did not come back up within 20s — check: journalctl -u printops-api.service -n 50" >&2
  exit 1
fi

echo "== Restarting printops-web.service =="
sudo systemctl restart printops-web.service
WEB_OK=false
for _ in $(seq 1 20); do
  if curl -sf -o /dev/null http://127.0.0.1:3000/ 2>/dev/null; then
    WEB_OK=true
    break
  fi
  sleep 1
done
if [ "$WEB_OK" != "true" ]; then
  echo "ERROR: printops-web.service did not come back up within 20s — check: journalctl -u printops-web.service -n 50" >&2
  exit 1
fi

echo "== Update complete: now on $(cat "$REPO_DIR/VERSION") =="
