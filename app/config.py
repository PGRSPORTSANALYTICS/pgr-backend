from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from jose import jwt
from jose.exceptions import JWTError
from datetime import datetime, timedelta
from typing import Optional, Dict
import uuid

from app.database import get_db
from app.models.user import User, AccessLevel
from app.config import get_settings
from app.services.audit import audit_service

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()


# -------------------------
# Schemas
# -------------------------
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


# -------------------------
# JWT helpers (SAME secret)
# -------------------------
def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()

    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not set")

    expire = datetime.utcnow() + timedelta(days=7)
    to_encode = {
        "sub": user_id,
        "email": email,
        "exp": expire,
    }

    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict]:
    settings = get_settings()

    if not settings.jwt_secret:
        return None

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except JWTError:
        return None
    except Exception:
        return None


# -------------------------
# Auth dependency
# -------------------------
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    # HTTPBearer ger redan bara själva token-strängen (utan "Bearer ")
    token = (credentials.credentials or "").strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


# -------------------------
# Routes
# -------------------------
@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    # hitta / skapa user
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

        await audit_service.log(
            db=db,
            event_type="user_created",
            source="auth",
            status="success",
            user_id=user.id,
        )

    token = create_access_token(user.id, user.email)

    await audit_service.log(
        db=db,
        event_type="user_login",
        source="auth",
        status="success",
        user_id=user.id,
    )

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
