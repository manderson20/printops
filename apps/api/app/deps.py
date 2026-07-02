from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError

from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.schemas.auth import UserOut

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    """Decodes the bearer token issued by the auth stub.

    TODO: replace with a real user store once local-account persistence exists.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        username = decode_access_token(token, settings)
    except PyJWTError as exc:
        raise credentials_exception from exc

    if username != settings.dev_username:
        raise credentials_exception

    return UserOut(username=username)
