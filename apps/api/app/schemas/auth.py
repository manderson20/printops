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
    logins, or the SSO user's email otherwise. `subject` is always the
    JWT's raw `sub` claim as-is (the dev username, or the SSO user's row
    id as a string — deliberately NOT the same as `username`/`email` in
    the SSO case) — kept so POST /auth/refresh can reissue a token with
    the identical subject rather than guessing at it from username/email."""

    username: str
    role: str
    email: str | None = None
    name: str | None = None
    subject: str
