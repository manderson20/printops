import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.impersonation import ImpersonationSession
from app.models.user import User
from app.schemas.auth import TokenResponse, UserOut
from app.schemas.user import (
    Role,
    UserAccountCreate,
    UserAccountOut,
    UserAccountPage,
    UserAccountUpdate,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])

# Short and fixed, independent of the admin-configured idle-timeout
# (SessionSettings.idle_timeout_minutes) — a "View as" session is meant to
# be a quick, supervised check, not a normal workday-length login.
IMPERSONATION_TOKEN_MINUTES = 20


@router.get("", response_model=UserAccountPage)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    role: Role | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if search:
        pattern = f"%{search}%"
        filters.append(or_(User.email.ilike(pattern), User.name.ilike(pattern)))
    if role:
        filters.append(User.role == role)

    count_stmt = select(func.count()).select_from(User)
    items_stmt = select(User).order_by(User.email)
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        items_stmt = items_stmt.where(condition)

    total = (await db.execute(count_stmt)).scalar_one()
    items_stmt = items_stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(items_stmt)).scalars().all()

    return UserAccountPage(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=UserAccountOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserAccountCreate, db: AsyncSession = Depends(get_db)):
    """Pre-provisions an account by email, google_sub left null until this
    person's first Google sign-in — see UserAccountCreate's docstring and
    app/routers/auth.py's google_callback, which matches this row by email
    on that first login instead of creating a duplicate."""
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Email is required"
        )

    user = User(
        email=email,
        role=payload.role,
        granted_ou_paths=payload.granted_ou_paths,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists"
        ) from exc
    await db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserAccountOut)
async def update_user(
    user_id: uuid.UUID, payload: UserAccountUpdate, db: AsyncSession = Depends(get_db)
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updates = payload.model_dump(exclude_unset=True)
    if "role" in updates and updates["role"] is not None:
        user.role = updates["role"]
    if "is_active" in updates and updates["is_active"] is not None:
        user.is_active = updates["is_active"]
    if "exempt_from_timeout" in updates and updates["exempt_from_timeout"] is not None:
        user.exempt_from_timeout = updates["exempt_from_timeout"]
    if "granted_ou_paths" in updates:
        user.granted_ou_paths = updates["granted_ou_paths"]

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/{user_id}/impersonate", response_model=TokenResponse)
async def impersonate_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Mints a short-lived, strictly read-only "View as" token for
    `user_id`, so an admin can verify what a given account actually sees
    — e.g. confirming a plain "viewer" really is scoped to just their own
    Insights + Print, not just that the nav hides the rest. Read-only is
    enforced centrally in app.main's block_impersonated_mutations, which
    403s any non-GET/HEAD/OPTIONS request carrying this token's
    `impersonated_by` claim — not by this endpoint or the frontend.

    The token itself is otherwise identical to what `user_id` would get
    from a real login (same role, email, name, granted_ou_paths), just
    short-lived (IMPERSONATION_TOKEN_MINUTES, not the admin-configured
    idle timeout) and non-refreshable (POST /auth/refresh is itself
    blocked by the same read-only middleware).

    Deliberately can't target another admin account — this tool exists to
    verify non-admin permission scoping, not to browse a peer admin's
    view, and skipping that case avoids ever needing to reason about an
    impersonated *admin* token's own blast radius."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can't impersonate a deactivated account.",
        )
    if target.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can't impersonate another admin account.",
        )

    admin_email = current_user.email or current_user.username
    admin_user_id = None
    if current_user.email:
        result = await db.execute(
            select(User.id).where(func.lower(User.email) == current_user.email.lower())
        )
        admin_user_id = result.scalar_one_or_none()

    extra_claims: dict[str, str | list[str]] = {
        "email": target.email,
        "name": target.name or "",
        "impersonated_by": admin_email,
    }
    if target.role == "ou_viewer":
        extra_claims["granted_ou_paths"] = target.granted_ou_paths or []

    token = create_access_token(
        subject=str(target.id),
        role=target.role,
        settings=settings,
        expires_minutes=IMPERSONATION_TOKEN_MINUTES,
        **extra_claims,
    )

    db.add(
        ImpersonationSession(
            admin_user_id=admin_user_id,
            admin_email=admin_email,
            target_user_id=target.id,
            target_email=target.email,
            target_role=target.role,
            expires_at=datetime.now(UTC) + timedelta(minutes=IMPERSONATION_TOKEN_MINUTES),
        )
    )
    await db.commit()

    return TokenResponse(access_token=token)
