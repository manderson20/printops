import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "viewer"]


class UserAccountOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: Role
    is_active: bool
    last_login_at: datetime | None


class UserAccountUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
