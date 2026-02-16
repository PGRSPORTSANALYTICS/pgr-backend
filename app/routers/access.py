from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.models import User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/access", tags=["access"])

class AccessStatusResponse(BaseModel):
    user_id: str
    access_level: str

@router.get("/status", response_model=AccessStatusResponse)
async def get_access_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return AccessStatusResponse(
        user_id=current_user.id,
        access_level=current_user.access_level
    )
