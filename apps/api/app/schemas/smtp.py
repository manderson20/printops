from pydantic import BaseModel, Field


class SmtpSettingsOut(BaseModel):
    enabled: bool
    host: str
    port: int
    username: str
    from_address: str
    from_name: str
    use_starttls: bool
    # Whether a password is stored, never which one. Same shape as
    # SnmpDefaultsOut's has_community — an admin needs to know whether they
    # still have to type it, not what it was.
    has_password: bool


class SmtpSettingsUpdate(BaseModel):
    enabled: bool | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    # Write-only. Omitted leaves the stored one alone, so an admin changing the
    # port does not have to re-enter it — and could not, since it is never
    # shown. An explicit empty string clears it, for a relay that takes none.
    password: str | None = None
    from_address: str | None = None
    from_name: str | None = None
    use_starttls: bool | None = None


class SmtpTestRequest(BaseModel):
    to: str = Field(min_length=3)
