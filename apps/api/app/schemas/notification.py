from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.notification import MAX_DELIVERY_ATTEMPTS


class NotificationSettingsOut(BaseModel):
    enabled: bool
    settle_minutes: int
    toner_low_percent: int
    notify_printer_attention: bool
    notify_printer_error: bool
    notify_toner_low: bool
    notify_quota_exceeded: bool

    model_config = {"from_attributes": True}


class NotificationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    # Up to a day. A settle window measured in days is a way of switching the
    # trigger off while believing it is on, and there is a switch for that.
    settle_minutes: int | None = Field(default=None, ge=0, le=1440)
    toner_low_percent: int | None = Field(default=None, ge=1, le=99)
    notify_printer_attention: bool | None = None
    notify_printer_error: bool | None = None
    notify_toner_low: bool | None = None
    notify_quota_exceeded: bool | None = None


class NotificationChannelOut(BaseModel):
    id: UUID
    kind: str
    name: str
    # Never the real URL. An incoming-webhook URL is a bearer credential
    # wearing a URL's clothes: anybody who can read it can post into that space
    # forever. It goes in write-only and comes back as a hint that identifies
    # which one it is without handing it over.
    target_hint: str
    enabled: bool
    last_error: str | None
    last_success_at: datetime | None


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1)
    kind: str = "webhook"
    enabled: bool = True

    @field_validator("target")
    @classmethod
    def _must_be_https(cls, value: str) -> str:
        """https only. The URL is a credential, and posting it in cleartext to
        somebody else's endpoint hands it to anything on the path."""
        if not value.startswith("https://"):
            raise ValueError("Webhook URLs must start with https://")
        return value

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value != "webhook":
            raise ValueError("Only 'webhook' channels are supported yet")
        return value


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    # Optional: an admin renaming a channel should not have to paste the URL
    # again, and cannot read it back to do so.
    target: str | None = None

    @field_validator("target")
    @classmethod
    def _must_be_https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("Webhook URLs must start with https://")
        return value


class NotificationEventOut(BaseModel):
    id: UUID
    created_at: datetime
    kind: str
    title: str
    body: str
    severity: str
    delivered_at: datetime | None
    attempts: int
    last_error: str | None
    # Derived rather than stored: "gave up" is a reading of attempts against
    # the cap, and storing it too would let the two disagree.
    gave_up: bool = False

    model_config = {"from_attributes": True}


class NotificationEventPage(BaseModel):
    events: list[NotificationEventOut]
    total: int


def event_out(event) -> NotificationEventOut:
    out = NotificationEventOut.model_validate(event)
    out.gave_up = event.delivered_at is None and event.attempts >= MAX_DELIVERY_ATTEMPTS
    return out


def channel_out(channel) -> NotificationChannelOut:
    """The last few characters only — enough to tell two channels apart on the
    settings page, not enough to reconstruct one."""
    target = channel.target or ""
    hint = f"…{target[-8:]}" if len(target) > 8 else "…"
    return NotificationChannelOut(
        id=channel.id,
        kind=channel.kind,
        name=channel.name,
        target_hint=hint,
        enabled=channel.enabled,
        last_error=channel.last_error,
        last_success_at=channel.last_success_at,
    )
