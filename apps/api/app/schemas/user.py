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


class UserAccountCreate(BaseModel):
    """Pre-provisions an account by email before its first Google sign-in
    — e.g. to grant someone admin (or OU Viewer + OUs) up front instead of
    them starting as Viewer and needing a promotion afterward. google_sub
    stays null until they actually sign in; /auth/google/callback matches
    this row by email (case-insensitive) on that first login and fills it
    in — see that function's docstring."""

    email: str
    role: Role = "viewer"
    granted_ou_paths: list[str] | None = None


class UserAccountPage(BaseModel):
    items: list[UserAccountOut]
    total: int
    page: int
    page_size: int
