from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

IdentityType = Literal[
    "staff_id", "pin", "badge_id", "department_id", "user_code", "vendor_user_id", "email"
]


class StaffCopierIdentityCreate(BaseModel):
    staff_email: str
    identity_type: IdentityType
    identity_value: str
    mfp_device_id: UUID | None = None
    note: str | None = None


class StaffCopierIdentityUpdate(BaseModel):
    identity_type: IdentityType | None = None
    identity_value: str | None = None
    mfp_device_id: UUID | None = None
    note: str | None = None


class StaffCopierIdentityOut(BaseModel):
    id: UUID
    staff_email: str
    identity_type: str
    identity_value: str
    mfp_device_id: UUID | None
    note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MissingStaffIdentityOut(BaseModel):
    """A roster member (see GoogleWorkspaceUser) with zero copier
    identities recorded yet — the "missing identity" warning list."""

    email: str
    name: str | None
    employee_id: str | None
