from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models.user import User
from app.models.subscription import Subscription
from app.routers.auth import get_current_user

router = APIRouter(prefix="/access", tags=["access"])

class AccessStatusResponse(BaseModel):
    user_id: str
    access_level: str
    subscription_status: Optional[str] = None
    subscription_plan: Optional[str] = None

@router.get("/status", response_model=AccessStatusResponse)
async def get_access_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    
    return AccessStatusResponse(
        user_id=current_user.id,
        access_level=current_user.access_level.value,
        subscription_status=subscription.status if subscription else None,
        subscription_plan=subscription.plan if subscription else None
    )
