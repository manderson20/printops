# Software update watcher

Backs the Updates admin page (`/updates` in the web app, `app/routers/updates.py`
in the API). An admin schedules an update from the browser; this is what actually
applies it.

## Why not just do it in the API request handler?

Applying an update restarts `printops-api.service` itself. A request handler that
restarts its own process can't reliably report success back to the caller — the
process is gone before the response finishes sending. So the actual pull/migrate/
build/restart work (`apply-update.sh`) runs outside the API entirely, kicked off by
a systemd timer polling a small "is anything due" endpoint
(`GET /api/v1/updates/status`, same `X-Backend-Token` trust boundary the CUPS
backend script uses — see `app/deps.py`'s `verify_backend_token`).

## Install (one-time, on the box running printops-api/web)

```bash
sudo cp infra/update-watcher/printops-update-watcher.service /etc/systemd/system/
sudo cp infra/update-watcher/printops-update-watcher.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now printops-update-watcher.timer
```

Requires `jq` (`sudo apt install jq` if not already present) and passwordless sudo
for the `itadmin` user to restart `printops-api.service`/`printops-web.service`
(already the case on this box — see `NOPASSWD` in `sudo -l`).

## What `apply-update.sh` actually does

1. Refuses to run if the working tree has uncommitted changes.
2. `git fetch` + `git merge --ff-only origin/main`.
3. `uv sync` (API deps) and `alembic upgrade head` (DB migrations).
4. `pnpm install` + `pnpm build` (web).
5. Restarts `printops-api.service`, then `printops-web.service`, each with a
   short health-check retry loop.

A failed run does **not** automatically roll back — it stops and reports the
failure (visible in the Updates page's history table and, for the full output,
`/var/log/printops-updates/`) so an admin can investigate over SSH before
re-scheduling.

## Checking it's running

```bash
systemctl status printops-update-watcher.timer
journalctl -u printops-update-watcher.service -n 50
```
