"""Sending one message through the configured relay.

stdlib smtplib rather than a new dependency: aiosmtplib is not in this project
and a notification volume measured in a handful a week does not justify adding
one. smtplib blocks, so the call runs in a thread — the same treatment
app/printers/queue_sync.py gives the CUPS scripts.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.crypto import decrypt
from app.models.smtp import SmtpSettings

# Longer than the webhook's 10s: a relay doing a DNS lookup, a TLS handshake
# and a greylisting pause is legitimately slower than an HTTP POST, and a
# timeout here costs a retry rather than a lost message.
SMTP_TIMEOUT_SECONDS = 30


class MailError(Exception):
    """Sending failed. `permanent` means the same message will fail again."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def _configured(settings: SmtpSettings | None) -> SmtpSettings:
    if settings is None or not settings.enabled:
        raise MailError("Email is not configured.", permanent=True)
    if not settings.host or not settings.from_address:
        raise MailError("Email needs a relay host and a from address.", permanent=True)
    return settings


def build_message(settings: SmtpSettings, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        f"{settings.from_name} <{settings.from_address}>"
        if settings.from_name
        else settings.from_address
    )
    message["To"] = to
    message.set_content(body)
    return message


def _send_blocking(settings: SmtpSettings, message: EmailMessage) -> None:
    password = decrypt(settings.password_encrypted) if settings.password_encrypted else None
    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if settings.use_starttls:
                smtp.starttls()
            if settings.username and password:
                smtp.login(settings.username, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        # Wrong credentials will be wrong next time too. Retrying a bad login
        # eight times is also a good way to get an account locked.
        raise MailError(f"Authentication rejected: {exc}", permanent=True) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(f"Recipient refused: {exc}", permanent=True) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailError(f"Sender refused: {exc}", permanent=True) from exc
    except (smtplib.SMTPException, OSError) as exc:
        # Connection refused, DNS failure, a relay having a bad afternoon — all
        # worth trying again.
        raise MailError(f"{type(exc).__name__}: {exc}") from exc


async def send_mail(settings: SmtpSettings | None, *, to: str, subject: str, body: str) -> None:
    """Send one message. Raises MailError on failure."""
    configured = _configured(settings)
    message = build_message(configured, to, subject, body)
    await asyncio.to_thread(_send_blocking, configured, message)
