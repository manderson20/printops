import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import decode_access_token
from app.db import get_db
from app.models.zabbix import ZabbixSettings
from app.schemas.auth import UserOut

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
backend_token_header = APIKeyHeader(name="X-Backend-Token")
zabbix_token_header = APIKeyHeader(name="X-Zabbix-Token")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    """Builds the current user entirely from JWT claims — both the dev
    break-glass login (app/routers/auth.py's /auth/login) and Google SSO
    (/auth/google/callback) embed role (and, for SSO, email/name) directly
    in the token at issuance time, so this never hits the DB. That means a
    role changed via the Users admin page only takes effect the next time
    the affected user logs in / their token expires, not immediately."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token, settings)
    except PyJWTError as exc:
        raise credentials_exception from exc

    subject = payload.get("sub")
    role = payload.get("role")
    if not subject or role not in ("admin", "viewer", "ou_viewer"):
        raise credentials_exception

    if subject == settings.dev_username:
        return UserOut(username=subject, role=role, subject=subject)

    email = payload.get("email")
    if not email:
        raise credentials_exception
    return UserOut(
        username=email,
        role=role,
        email=email,
        name=payload.get("name"),
        subject=subject,
        # Display-only — never trusted for enforcement, which always
        # re-reads the User row fresh (see reports.py's _report_filters).
        granted_ou_paths=payload.get("granted_ou_paths"),
    )


def require_role(*roles: str):
    """FastAPI dependency factory — 403s unless the current user's role is
    one of `roles`. The single, central place role checks happen, per
    ARCHITECTURE.md §5 ("checked centrally via an API dependency, not ad
    hoc per router"). Usage: Depends(require_role("admin"))."""

    def _check(current_user: UserOut = Depends(get_current_user)) -> UserOut:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to perform this action.",
            )
        return current_user

    return _check


def verify_backend_token(
    token: str = Depends(backend_token_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """Authenticates service-to-service calls (the CUPS backend script), not
    user sessions — separate from get_current_user/JWT auth entirely."""
    if not secrets.compare_digest(token, settings.backend_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid backend token",
        )


async def verify_zabbix_token(
    token: str = Depends(zabbix_token_header),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Authenticates an external Zabbix server's polls against
    /api/v1/integrations/zabbix/* only — deliberately separate from
    get_current_user/require_role/verify_backend_token. Nothing else in
    the API accepts this token, so it's least-privilege by construction,
    no RBAC role needed. Checked against the DB (not a static env secret
    like verify_backend_token) since it must be admin-rotatable entirely
    from the Integrations UI, per that page's no-CLI requirement.
    Disabled or never-configured settings reject unconditionally — the
    Integrations page's "Enabled" toggle is a real kill switch, not just
    a UI hint."""
    result = await db.execute(select(ZabbixSettings).limit(1))
    settings = result.scalar_one_or_none()
    if (
        settings is None
        or not settings.enabled
        or not settings.api_token
        or not secrets.compare_digest(token, settings.api_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Zabbix token",
        )
