from pydantic import BaseModel


class SessionSettingsOut(BaseModel):
    idle_timeout_minutes: int


class SessionSettingsUpdate(BaseModel):
    idle_timeout_minutes: int | None = None
