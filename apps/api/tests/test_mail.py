"""Sending mail, and refusing to when it cannot work.

The failure that matters here is not a bounced message — it is a relay that is
half-configured or wrongly credentialled, and a notifier that reacts to that by
retrying eight times. A wrong password retried eight times is also a good way
to get an account locked, which turns a notification problem into an account
problem.
"""

import smtplib

import pytest

from app.core.crypto import encrypt
from app.mail import send as mail
from app.mail.send import MailError, build_message, send_mail
from app.models.smtp import SmtpSettings


def configured(**overrides) -> SmtpSettings:
    values = {
        "enabled": True,
        "host": "smtp.example.org",
        "port": 587,
        "username": "printops@example.org",
        "password_encrypted": encrypt("hunter2"),
        "from_address": "printops@example.org",
        "from_name": "PrintOps",
        "use_starttls": True,
    }
    values.update(overrides)
    return SmtpSettings(**values)


class FakeSMTP:
    """Records what a real relay would have been asked to do."""

    instances: list["FakeSMTP"] = []
    # Class-level rather than wrapping __init__ per test: an earlier version
    # rebound __init__ cumulatively, so a login failure primed in one test
    # leaked into every later one and two tests passed for the wrong reason.
    next_login_error: Exception | None = None
    next_send_error: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        self.raise_on_login = FakeSMTP.next_login_error
        self.raise_on_send = FakeSMTP.next_send_error
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        if self.raise_on_login:
            raise self.raise_on_login
        self.login_args = (username, password)

    def send_message(self, message):
        if self.raise_on_send:
            raise self.raise_on_send
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.next_login_error = None
    FakeSMTP.next_send_error = None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


# --- refusing to send -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "because"),
    [
        (None, "no settings row at all"),
        (
            SmtpSettings(enabled=False, host="smtp.example.org", from_address="a@b.org"),
            "switched off",
        ),
        (SmtpSettings(enabled=True, host="", from_address="a@b.org"), "no relay host"),
        (SmtpSettings(enabled=True, host="smtp.example.org", from_address=""), "no from address"),
    ],
)
async def test_a_half_configured_relay_refuses_permanently(settings, because, smtp):
    """Permanently, so the notifier retires the message instead of retrying it
    eight times against a relay that was never going to work."""
    with pytest.raises(MailError) as caught:
        await send_mail(settings, to="admin@example.org", subject="s", body="b")

    assert caught.value.permanent is True, because
    assert smtp.instances == [], "should not have opened a connection"


# --- sending ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_message_goes_out_with_tls_and_credentials(smtp):
    await send_mail(
        configured(), to="admin@example.org", subject="ES Library: media-jam", body="Paper jam"
    )

    sent = smtp.instances[0]
    assert (sent.host, sent.port) == ("smtp.example.org", 587)
    assert sent.started_tls is True
    # Decrypted at the point of use, never held in the settings object.
    assert sent.login_args == ("printops@example.org", "hunter2")
    assert len(sent.sent) == 1


@pytest.mark.asyncio
async def test_starttls_can_be_turned_off_for_an_on_premises_relay(smtp):
    await send_mail(configured(use_starttls=False), to="a@b.org", subject="s", body="b")
    assert smtp.instances[0].started_tls is False


@pytest.mark.asyncio
async def test_a_relay_that_wants_no_credentials_is_not_given_any(smtp):
    await send_mail(
        configured(username="", password_encrypted=None), to="a@b.org", subject="s", body="b"
    )
    assert smtp.instances[0].login_args is None


def test_the_from_header_carries_a_name_when_there_is_one():
    """ "PrintOps <printops@district.org>" reads better in a crowded inbox than a
    bare address, and an alert nobody recognises is an alert nobody opens."""
    with_name = build_message(configured(), "a@b.org", "subject", "body")
    assert with_name["From"] == "PrintOps <printops@example.org>"

    without = build_message(configured(from_name=""), "a@b.org", "subject", "body")
    assert without["From"] == "printops@example.org"


def test_the_subject_is_the_title_alone():
    """It is what shows in a notification bar; a "PrintOps:" prefix would push
    the useful half off the end on a phone."""
    message = build_message(configured(), "a@b.org", "ES Library: media-jam", "body")
    assert message["Subject"] == "ES Library: media-jam"


# --- failures ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rejected_login_is_permanent(smtp):
    """Wrong credentials will be wrong next time. Retrying eight times is also a
    good way to get the account locked, turning a notification problem into an
    account problem."""
    _prime(smtp, raise_on_login=smtplib.SMTPAuthenticationError(535, b"nope"))

    with pytest.raises(MailError) as caught:
        await send_mail(configured(), to="a@b.org", subject="s", body="b")
    assert caught.value.permanent is True


@pytest.mark.asyncio
async def test_a_refused_recipient_is_permanent(smtp):
    _prime(smtp, raise_on_send=smtplib.SMTPRecipientsRefused({"a@b.org": (550, b"no")}))

    with pytest.raises(MailError) as caught:
        await send_mail(configured(), to="a@b.org", subject="s", body="b")
    assert caught.value.permanent is True


@pytest.mark.asyncio
async def test_a_connection_problem_is_worth_retrying(smtp):
    """A relay having a bad afternoon is not the same as a wrong password."""
    _prime(smtp, raise_on_send=smtplib.SMTPServerDisconnected("connection lost"))

    with pytest.raises(MailError) as caught:
        await send_mail(configured(), to="a@b.org", subject="s", body="b")
    assert caught.value.permanent is False


@pytest.mark.asyncio
async def test_an_unreachable_relay_is_worth_retrying(smtp):
    _prime(smtp, raise_on_send=OSError("connection refused"))

    with pytest.raises(MailError) as caught:
        await send_mail(configured(), to="a@b.org", subject="s", body="b")
    assert caught.value.permanent is False


def _prime(smtp, *, raise_on_login=None, raise_on_send=None):
    """Makes the next constructed FakeSMTP fail in the given way."""
    smtp.next_login_error = raise_on_login
    smtp.next_send_error = raise_on_send
