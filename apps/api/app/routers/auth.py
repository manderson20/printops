import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt
from app.core.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.deps import get_current_user
from app.integrations.google_oauth import (
    GoogleOAuthError,
    build_authorization_url,
    exchange_code,
    verify_id_token,
)
from app.models.google_sso import GoogleSsoSettings
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter()

# TODO: replace with a real user store (local accounts table) once the DB layer lands.
# This stub proves the JWT login -> bearer-token -> /auth/me round trip and nothing else.

STATE_COOKIE_NAME = "printops_oauth_state"
STATE_COOKIE_MAX_AGE_SECONDS = 5 * 60


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
    token = create_access_token(subject=payload.username, role="admin", settings=settings)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current_user: UserOut = Depends(get_current_user)):
    return current_user


async def _get_google_sso_settings(db: AsyncSession) -> GoogleSsoSettings | None:
    result = await db.execute(select(GoogleSsoSettings).limit(1))
    return result.scalar_one_or_none()


def _admin_emails(sso: GoogleSsoSettings) -> list[str]:
    if not sso.initial_admin_emails:
        return []
    return [email.strip() for email in sso.initial_admin_emails.split(",") if email.strip()]


def _fail(message: str) -> RedirectResponse:
    """Redirects back into the SPA with the outcome in a URL *fragment*,
    not a query param, so a session token never ends up in server logs or
    the Referer header. A *relative* path — the browser is already doing a
    same-origin top-level navigation here, so there's no need for a
    configured frontend URL just to bounce back to the SPA. Deliberately
    NOT under /auth/* — Caddy proxies that whole prefix to this API, so a
    frontend page living there would be unreachable (confirmed the hard
    way: it 404'd from FastAPI instead of ever reaching Next.js)."""
    fragment = urlencode({"error": message})
    return RedirectResponse(f"/login/callback#{fragment}")


@router.get("/google/login")
async def google_login(db: AsyncSession = Depends(get_db)):
    """Starts the Google SSO flow: sets a short-lived random `state` as a
    cookie (standard CSRF protection for OAuth redirect flows — no
    server-side session store needed) and redirects to Google's consent
    screen with that same state. Configured entirely via the Integrations
    UI (Settings -> Google Sign-In), not env vars — see app/routers/settings.py's
    /google-sso endpoints."""
    sso = await _get_google_sso_settings(db)
    if not sso or not sso.enabled or not sso.client_id or not sso.redirect_base_url:
        return _fail("Google sign-in is not configured yet.")

    state = secrets.token_urlsafe(32)
    redirect = RedirectResponse(
        build_authorization_url(
            client_id=sso.client_id,
            redirect_uri=f"{sso.redirect_base_url}/auth/google/callback",
            state=state,
        )
    )
    redirect.set_cookie(
        STATE_COOKIE_NAME,
        state,
        max_age=STATE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return redirect


@router.get("/google/callback")
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    printops_oauth_state: str | None = Cookie(None),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    """Exchanges the authorization code, verifies the id_token, and
    upserts a User by google_sub — new users default to "viewer" unless
    their email is in sso.initial_admin_emails, existing users' role is
    never touched here (only the Users admin page changes it)."""
    sso = await _get_google_sso_settings(db)
    if not sso or not sso.enabled or not sso.client_id or not sso.client_secret_encrypted or not sso.redirect_base_url:
        return _fail("Google sign-in is not configured.")

    state_ok = state and printops_oauth_state and secrets.compare_digest(state, printops_oauth_state)
    if not state_ok:
        return _fail("Login expired or invalid — please try again.")
    if not code:
        return _fail("Google did not return an authorization code.")

    redirect_uri = f"{sso.redirect_base_url}/auth/google/callback"
    try:
        token_response = await exchange_code(
            code=code,
            client_id=sso.client_id,
            client_secret=decrypt(sso.client_secret_encrypted),
            redirect_uri=redirect_uri,
        )
        claims = verify_id_token(token_response["id_token"], client_id=sso.client_id)
    except GoogleOAuthError as exc:
        return _fail(str(exc))

    if not claims.get("email_verified"):
        return _fail("Your Google account's email is not verified.")
    if claims.get("hd") != sso.workspace_domain:
        return _fail("Sign-in is restricted to this organization's Google Workspace accounts.")

    google_sub = claims["sub"]
    email = claims["email"]
    result = await db.execute(select(User).where(User.google_sub == google_sub))
    user = result.scalar_one_or_none()
    if user is None:
        role = "admin" if email in _admin_emails(sso) else "viewer"
        user = User(google_sub=google_sub, email=email, role=role)
        db.add(user)

    user.email = email
    user.name = claims.get("name")
    user.picture_url = claims.get("picture")
    user.last_login_at = datetime.now(UTC)
    await db.commit()

    if not user.is_active:
        return _fail("Your account has been deactivated. Contact an administrator.")

    token = create_access_token(
        subject=str(user.id),
        role=user.role,
        settings=settings,
        email=user.email,
        name=user.name or "",
    )
    fragment = urlencode({"token": token})
    return RedirectResponse(f"/login/callback#{fragment}")
