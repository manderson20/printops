import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "viewer", "ou_viewer"]


class UserAccountOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: str
    name: str | None
    role: Role
    is_active: bool
    last_login_at: datetime | None
    exempt_from_timeout: bool
    granted_ou_paths: list[str] | None


class UserAccountUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    exempt_from_timeout: bool | None = None
    granted_ou_paths: list[str] | None = None


class UserAccountPage(BaseModel):
    items: list[UserAccountOut]
    total: int
    page: int
    page_size: int
