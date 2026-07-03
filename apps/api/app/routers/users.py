import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_role
from app.models.user import User
from app.schemas.user import UserAccountOut, UserAccountUpdate

router = APIRouter(dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[UserAccountOut])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.email))
    return result.scalars().all()


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
