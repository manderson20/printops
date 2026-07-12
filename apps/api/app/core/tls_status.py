"""Reads the TLS certificate CUPS's client-facing listener currently uses
(scripts/sync_server_settings.sh copies it there from Caddy's own
certificate storage) — a read-only status display for Settings > Server,
computed live from the file rather than stored in the DB, since the file
is the actual source of truth and changes independently on Caddy's own
renewal schedule."""

from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509

# NOT /etc/cups/ssl/printops-managed.crt — that directory is deliberately
# 700 root:lp (it holds the private key CUPS itself uses), unreadable by
# this API process (runs as itadmin, non-root). scripts/sync_server_settings.sh
# keeps a second, itadmin-readable copy of just the public cert here
# specifically so this status read doesn't need CUPS's own security
# boundary loosened — confirmed live: reading straight from /etc/cups/ssl
# silently returned "no certificate" (a permission error swallowed by the
# broad `except OSError` below), not an error, until this was split out.
MANAGED_CERT_PATH = Path("/var/lib/printops/tls-status/printops-managed.crt")


class TlsCertificateStatus:
    def __init__(self, issuer: str, expires_at: datetime, days_remaining: int) -> None:
        self.issuer = issuer
        self.expires_at = expires_at
        self.days_remaining = days_remaining


def read_certificate_status(path: Path = MANAGED_CERT_PATH) -> TlsCertificateStatus | None:
    """None if nothing has been synced yet (first-run, or the sync script
    has never succeeded) — the UI shows a "not yet synced" state for that,
    rather than treating it as an error."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        cert = x509.load_pem_x509_certificate(raw)
    except ValueError:
        return None
    expires_at = cert.not_valid_after_utc
    days_remaining = (expires_at - datetime.now(UTC)).days
    issuer = cert.issuer.rfc4514_string()
    return TlsCertificateStatus(issuer=issuer, expires_at=expires_at, days_remaining=days_remaining)
