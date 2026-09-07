from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.notification import MAX_DELIVERY_ATTEMPTS


def validate_target(kind: str, target: str) -> str:
    """What a channel of this kind can actually send to.

    https only for a webhook: the URL is a bearer credential, and posting it in
    cleartext to somebody else's endpoint hands it to anything on the path.
    """
    target = target.strip()
    if kind == "webhook":
        if not target.startswith("https://"):
            raise ValueError("Webhook URLs must start with https://")
    elif kind == "email":
        if "@" not in target or target.startswith("@") or target.endswith("@"):
            raise ValueError("That does not look like an email address")
    return target


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
    # A webhook URL never comes back: it is a bearer credential wearing a URL's
    # clothes, so it goes in write-only and returns only enough to tell two
    # channels apart. An email address does come back in full — it is a
    # destination, not a credential, and an admin needs to see who is being
    # copied.
    target_hint: str
    enabled: bool
    last_error: str | None
    last_success_at: datetime | None


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1)
    kind: str = "webhook"
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in ("webhook", "email"):
            raise ValueError("kind must be 'webhook' or 'email'")
        return value

    @model_validator(mode="after")
    def _target_matches_kind(self):
        """A webhook URL in the email box, or an address in the webhook box,
        fails at the first real incident rather than here. Cheaper to say so
        now, while somebody is looking at the form."""
        self.target = validate_target(self.kind, self.target)
        return self


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    # Optional: an admin renaming a channel should not have to paste the URL
    # again, and cannot read it back to do so.
    target: str | None = None

    @field_validator("target")
    @classmethod
    def _plausible(cls, value: str | None) -> str | None:
        """Kind-agnostic here: an update does not carry the kind, and the
        router re-checks against the stored one before saving."""
        if value is None:
            return None
        if not value.startswith("https://") and "@" not in value:
            raise ValueError("Expected an https:// webhook URL or an email address")
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
    """Enough to tell two channels apart on the settings page, not enough to
    reconstruct one.

    An email address is shown in full: it is a destination, not a credential,
    and an admin needs to see which address a copy is going to. A webhook URL
    is the opposite — anybody holding it can post into that space.
    """
    target = channel.target or ""
    if channel.kind == "email":
        hint = target
    else:
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
