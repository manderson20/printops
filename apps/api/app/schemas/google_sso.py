from pydantic import BaseModel


class GoogleSsoSettingsUpdate(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    workspace_domain: str | None = None
    initial_admin_emails: list[str] | None = None
    redirect_base_url: str | None = None
    enabled: bool | None = None


class GoogleSsoSettingsOut(BaseModel):
    """Never returns the decrypted client secret — has_client_secret
    indicates whether it's set, matching the Mosyle/ClassGuard/Google
    Workspace masking pattern."""

    client_id: str | None
    has_client_secret: bool
    workspace_domain: str | None
    initial_admin_emails: list[str]
    redirect_base_url: str | None
    enabled: bool
