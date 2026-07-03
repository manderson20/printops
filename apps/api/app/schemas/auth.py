from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    """Built entirely from JWT claims (see app.deps.get_current_user), not
    a DB lookup — `username` is the dev account's username for break-glass
    logins, or the SSO user's email otherwise."""

    username: str
    role: str
    email: str | None = None
    name: str | None = None
