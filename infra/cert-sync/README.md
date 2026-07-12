# Server settings / certificate sync

Backs Settings > Server (`/settings/server` in the web app, `app/routers/settings.py`'s
`/settings/server` section). Applies the current hostname/TLS configuration to
`cupsd.conf` and every printer's Avahi service file — see
`scripts/sync_server_settings.sh` for exactly what it does.

## Why a separate timer, not just "on save"?

`PUT /api/v1/settings/server` already runs the same sync script synchronously, so a
change an admin makes takes effect immediately. This timer exists for the case
nobody touched the settings page at all: Caddy (fronting the PrintOps web app, see
`infra/Caddyfile.template`) renews its own Let's Encrypt certificate automatically,
roughly every 60 days. Without this timer, CUPS would keep using whatever cert was
last copied until an admin happened to revisit Settings > Server and hit Save again.
Daily is far more often than actually needed — just cheap, and matches
`infra/update-watcher/`'s timer+oneshot-service precedent (this repo has no cron and
no file-watch/path-unit convention).

## Install (one-time, on the box running printops-api/web/Caddy)

```bash
sudo cp infra/cert-sync/printops-cert-sync.service /etc/systemd/system/
sudo cp infra/cert-sync/printops-cert-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now printops-cert-sync.timer
```

Requires passwordless sudo for the `itadmin` user (already the case on this box —
same as `sync_cups_queue.sh`/`infra/update-watcher/` already rely on) to restart
`cups.service` and read/write `/etc/cups/cupsd.conf`, `/etc/cups/ssl/`, and Caddy's
certificate storage under `/var/lib/caddy/`. Note the sync script does a full
`systemctl restart cups.service`, not just a config reload — confirmed live that
CUPS 2.x only picks up a changed `ServerName` or a newly-matching certificate file
on a real restart, not `systemctl reload`/`SIGHUP`.

## What it does, and what it deliberately doesn't

`scripts/sync_server_settings.sh` always keeps CUPS's certificate fresh and sets
`ServerName` so CUPS stops rejecting requests for the configured hostname — both are
pure improvements with no failure mode for an existing plaintext client. The cert
itself has to overwrite the file CUPS auto-generated for its own OS-level hostname
(`hostname -f`) — that's the file CUPS's TLS layer actually looks up, independent of
`ServerName` — see the script's own comments for how this was confirmed. It does
**not** turn on `DefaultEncryption Required` or the `_ipps._tcp` Bonjour
advertisement unless an admin has explicitly opted into those on the Settings >
Server page — both stay off by default, since those two (unlike the cert/hostname
fix) are the changes that could actually break an unusual client.
