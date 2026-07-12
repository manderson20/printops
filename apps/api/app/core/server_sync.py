import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts"
SYNC_SCRIPT = SCRIPTS_DIR / "sync_server_settings.sh"

# Copies a cert from Caddy's storage, rewrites cupsd.conf's managed block,
# reloads cupsd, then regenerates every printer's Avahi service file — more
# steps than a single printer's queue sync (app/printers/queue_sync.py), so
# a slightly longer ceiling.
SYNC_TIMEOUT_SECONDS = 60


class ServerSyncError(Exception):
    pass


def sync_server_settings() -> None:
    """Applies the current ServerSettings row to cupsd.conf/Avahi — see
    scripts/sync_server_settings.sh. Raises ServerSyncError on failure;
    callers should record this non-fatally (ServerSettings.sync_error),
    same convention as Printer.queue_sync_error, not block the settings
    save over it. Also run independently on a daily systemd timer
    (infra/cert-sync/) to pick up Caddy's own certificate renewals without
    needing an admin to revisit this settings page."""
    try:
        result = subprocess.run(
            [str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=SYNC_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ServerSyncError("sync_server_settings.sh not found on the PrintOps server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServerSyncError(
            f"sync_server_settings.sh timed out after {SYNC_TIMEOUT_SECONDS}s."
        ) from exc

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        raise ServerSyncError(reason or f"sync_server_settings.sh exited {result.returncode}.")
