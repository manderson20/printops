from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.deps import get_current_user
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter()

# TODO: replace with a real user store (local accounts table) once the DB layer lands.
# This stub proves the JWT login -> bearer-token -> /auth/me round trip and nothing else.


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, settings: Settings = Depends(get_settings)):
    dev_password_hash = hash_password(settings.dev_password)
    valid = payload.username == settings.dev_username and verify_password(
        payload.password, dev_password_hash
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token(subject=payload.username, settings=settings)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user: UserOut = Depends(get_current_user)):
    return current_user
