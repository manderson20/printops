from datetime import datetime

from pydantic import BaseModel

from app.schemas.staff_copier_identity import IdentityType


class GoogleWorkspaceSettingsUpdate(BaseModel):
    service_account_json: str | None = None
    admin_email: str | None = None
    customer_id: str | None = None
    enabled: bool | None = None
    staff_org_unit_path: str | None = None
    # Mirrors GoogleWorkspaceUser.employee_id into a StaffCopierIdentity
    # automatically on every sync — off by default (see
    # app/integrations/google_workspace.py:_refresh_google_sourced_copier_identities).
    auto_create_copier_identity_from_employee_id: bool | None = None
    auto_copier_identity_type: IdentityType | None = None


class GoogleWorkspaceSettingsOut(BaseModel):
    """Never returns the decrypted service account key — has_service_account_json
    indicates whether it's set, matching the Mosyle/ClassGuard masking pattern."""

    admin_email: str | None
    customer_id: str
    has_service_account_json: bool
    enabled: bool
    last_synced_at: datetime | None
    last_sync_error: str | None
    device_count: int
    staff_org_unit_path: str | None
    auto_create_copier_identity_from_employee_id: bool
    auto_copier_identity_type: IdentityType


class GoogleWorkspaceTestResult(BaseModel):
    ok: bool
    device_count: int | None = None
    error: str | None = None


class GoogleWorkspaceUserOut(BaseModel):
    email: str
    name: str | None
    employee_id: str | None = None
    aliases: list[str] | None = None
