import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from jose import jwt
from jose.exceptions import JWTError
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User, AccessLevel


router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()


class LoginRequest(BaseModel):
    email: EmailStr


class LoginResponse(BaseModel):
    token: str
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: str
    email: str
    access_level: str
    created_at: datetime


def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="JWT secret missing (JWT_SECRET or SESSION_SECRET).")

    expire = datetime.utcnow() + timedelta(days=7)
    to_encode = {"sub": str(user_id), "email": email, "exp": expire}

    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    settings = get_settings()
    if not settings.jwt_secret:
        return None
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    # Läser Authorization header manuellt (som du redan gör)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    # Stöd både "Bearer <token>" och rå token (ifall Swagger strular)
    token = auth_header.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=str(uuid.uuid4()),
            email=request.email,
            access_level=AccessLevel.FREE,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(str(user.id), user.email)
    return LoginResponse(token=token, user_id=str(user.id), email=user.email)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    access_level = (
        current_user.access_level.value
        if hasattr(current_user.access_level, "value")
        else str(current_user.access_level)
    )

    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        access_level=access_level,
        created_at=current_user.created_at,
    )
