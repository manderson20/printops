#!/usr/bin/env bash
# Applies the current ServerSettings row (hostname, TLS toggles) to
# cupsd.conf and every printer's Avahi service file.
#
# Usage: ./scripts/sync_server_settings.sh
#
# Invoked two ways (see app/core/server_sync.py's docstring):
#  1. Synchronously after PUT /api/v1/settings/server, so a hostname/TLS
#     change takes effect immediately.
#  2. On a daily systemd timer (infra/cert-sync/), to pick up Caddy's own
#     Let's Encrypt renewals without needing an admin to revisit the
#     settings page — Caddy renews ~30 days before its 90-day certs expire,
#     so daily is far more often than needed, just cheap and simple
#     (matches infra/update-watcher/'s timer+oneshot precedent; this repo
#     has no cron and no file-watch/path-unit precedent).
#
# Safe to also run manually/standalone, same convention as
# sync_cups_queue.sh.

set -euo pipefail

API_BASE="${PRINTOPS_API_BASE:-http://localhost:8000}"
ENV_FILE="${PRINTOPS_ENV_FILE:-/home/itadmin/printops/apps/api/.env}"
CUPSD_CONF="/etc/cups/cupsd.conf"
# CUPS 2.x has no ServerCertificate/ServerKey directive (that's CUPS 1.x/
# legacy) — TLS cert selection instead uses whatever file in ServerKeychain
# (cups-files.conf, defaults to /etc/cups/ssl) matches the box's OS-level
# hostname (`hostname -f`) — confirmed live via cupsd debug logging
# (cupsdAddCert / DNS_SD entries all showed "@printops-dev" regardless of
# cupsd.conf's ServerName). This is a *separate* mechanism from Host-header
# validation (which ServerName below does control, confirmed by the 400->200
# fix) — setting ServerName alone, or dropping a correctly-named cert under
# a DIFFERENT filename, does nothing for which cert gets presented. So the
# managed cert has to overwrite the file CUPS already auto-generated for its
# own system hostname, not a new file named after the admin-configured one.
SSL_DIR="/etc/cups/ssl"
SYSTEM_HOSTNAME=$(hostname -f)
# Itadmin-readable copy of just the public cert — see the comment at the
# copy step below for why this exists (this one's filename doesn't need to
# follow CUPS's convention, since cupsd never reads it).
STATUS_DIR="/var/lib/printops/tls-status"
CADDY_CERT_ROOT="/var/lib/caddy/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory"
MARKER_BEGIN="# BEGIN PRINTOPS MANAGED — do not edit by hand, see scripts/sync_server_settings.sh"
MARKER_END="# END PRINTOPS MANAGED"

TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$ENV_FILE" | cut -d= -f2)

SETTINGS_JSON=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/server-settings")

HOSTNAME_VALUE=$(python3 -c "import json,sys; print(json.load(sys.stdin)['hostname'])" <<<"$SETTINGS_JSON")
REQUIRE_ENCRYPTION=$(python3 -c "import json,sys; print('true' if json.load(sys.stdin)['require_encryption'] else 'false')" <<<"$SETTINGS_JSON")

if [ -z "$HOSTNAME_VALUE" ]; then
    echo "ERROR: sync_server_settings: no hostname configured (Settings > Server) — nothing to sync." >&2
    exit 1
fi

# Named after the system hostname (what CUPS actually looks up), not the
# admin-configured PrintOps hostname — see the comment above SSL_DIR.
MANAGED_CERT="${SSL_DIR}/${SYSTEM_HOSTNAME}.crt"
MANAGED_KEY="${SSL_DIR}/${SYSTEM_HOSTNAME}.key"
# One-time safety net: preserve CUPS's own originally auto-generated
# self-signed cert before the first overwrite, in case this ever needs
# reverting. Only taken once — a second run finding a .orig already there
# leaves it alone rather than clobbering it with an already-real cert.
if sudo test -f "$MANAGED_CERT" && ! sudo test -f "${MANAGED_CERT}.orig"; then
    sudo cp -p "$MANAGED_CERT" "${MANAGED_CERT}.orig"
    sudo cp -p "$MANAGED_KEY" "${MANAGED_KEY}.orig"
fi

# Step 1: sync the cert from Caddy's storage, if Caddy actually holds one
# for this hostname yet (it won't on a box that doesn't run Caddy, or
# before the first successful ACME issuance) — best-effort, not fatal,
# since the hostname/ServerName fix below is still worth applying even
# without a cert (CUPS falls back to auto-generating its own self-signed
# one for whatever ServerName is now configured, same as it already does
# today for its default name). Caddy's own storage is keyed by the
# admin-configured PrintOps hostname (that's the domain its Caddyfile
# actually requests a cert for), not the system hostname.
CADDY_CERT="${CADDY_CERT_ROOT}/${HOSTNAME_VALUE}/${HOSTNAME_VALUE}.crt"
CADDY_KEY="${CADDY_CERT_ROOT}/${HOSTNAME_VALUE}/${HOSTNAME_VALUE}.key"
if sudo test -f "$CADDY_CERT" && sudo test -f "$CADDY_KEY"; then
    sudo mkdir -p "$SSL_DIR"
    sudo cp "$CADDY_CERT" "$MANAGED_CERT"
    sudo cp "$CADDY_KEY" "$MANAGED_KEY"
    sudo chmod 644 "$MANAGED_CERT"
    sudo chmod 600 "$MANAGED_KEY"
    # /etc/cups/ssl is deliberately 700 root:lp (it holds private keys) —
    # the API process (running as itadmin, non-root) can never read a cert
    # from there for the Settings > Server status display. Rather than
    # loosen CUPS's own directory permissions, keep a second copy of just
    # the PUBLIC cert (never the key) somewhere itadmin can read —
    # app/core/tls_status.py reads from here, not /etc/cups/ssl.
    sudo mkdir -p "$STATUS_DIR"
    sudo cp "$CADDY_CERT" "$STATUS_DIR/printops-managed.crt"
    sudo chown itadmin:itadmin "$STATUS_DIR/printops-managed.crt"
    sudo chmod 644 "$STATUS_DIR/printops-managed.crt"
    echo "Synced certificate for $HOSTNAME_VALUE from Caddy."
else
    echo "WARNING: no Caddy-issued certificate found for $HOSTNAME_VALUE at $CADDY_CERT — cupsd will auto-generate its own self-signed cert for this ServerName instead." >&2
fi

# Step 2: rewrite the managed block in cupsd.conf — fully regenerated each
# run (strip any prior managed block, append a fresh one) rather than
# sed-patching individual directives in place, so this is idempotent
# regardless of what a previous run (or manual edit) left behind.
DEFAULT_ENCRYPTION="IfRequested"
if [ "$REQUIRE_ENCRYPTION" = "true" ]; then
    DEFAULT_ENCRYPTION="Required"
fi

NEW_CONF=$(python3 -c "
import sys

marker_begin = sys.argv[1]
marker_end = sys.argv[2]
hostname = sys.argv[3]
default_encryption = sys.argv[4]

with open('$CUPSD_CONF') as f:
    content = f.read()

if marker_begin in content:
    before, rest = content.split(marker_begin, 1)
    _, after = rest.split(marker_end, 1)
    content = before.rstrip() + '\n' + after.lstrip()

block_lines = [
    marker_begin,
    f'ServerName {hostname}',
    f'DefaultEncryption {default_encryption}',
    marker_end,
]

content = content.rstrip() + '\n\n' + '\n'.join(block_lines) + '\n'
sys.stdout.write(content)
" "$MARKER_BEGIN" "$MARKER_END" "$HOSTNAME_VALUE" "$DEFAULT_ENCRYPTION")

echo "$NEW_CONF" | sudo tee "$CUPSD_CONF" > /dev/null

# Step 3: a plain reload/HUP does NOT pick up a changed ServerName or a
# newly-matching cert file (confirmed live) — only a full restart does.
# Brief interruption to the daemon, not just a config re-read.
sudo systemctl restart cups.service

echo "Applied ServerName=$HOSTNAME_VALUE, DefaultEncryption=$DEFAULT_ENCRYPTION to $CUPSD_CONF, restarted cups."

# Step 4: regenerate every active printer's Avahi service file, so
# advertise_ipps takes effect fleet-wide immediately rather than waiting
# for each printer's next unrelated sync.
PRINTER_IDS=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/printers/ids" \
  | python3 -c "import json,sys; print('\n'.join(p['id'] for p in json.load(sys.stdin)))")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while IFS= read -r printer_id; do
    [ -z "$printer_id" ] && continue
    sudo python3 "${SCRIPT_DIR}/../infra/cups/generate_avahi_service.py" "$printer_id" || \
        echo "WARNING: could not regenerate Avahi service for printer $printer_id" >&2
done <<<"$PRINTER_IDS"

echo "Done."
