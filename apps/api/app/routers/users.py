import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.user import User
from app.schemas.user import UserAccountOut, UserAccountPage, UserAccountUpdate

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=UserAccountPage)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if search:
        pattern = f"%{search}%"
        filters.append(or_(User.email.ilike(pattern), User.name.ilike(pattern)))

    count_stmt = select(func.count()).select_from(User)
    items_stmt = select(User).order_by(User.email)
    for condition in filters:
        count_stmt = count_stmt.where(condition)
        items_stmt = items_stmt.where(condition)

    total = (await db.execute(count_stmt)).scalar_one()
    items_stmt = items_stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(items_stmt)).scalars().all()

    return UserAccountPage(items=items, total=total, page=page, page_size=page_size)


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

    await db.commit()
    await db.refresh(user)
    return user
