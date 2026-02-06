from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from jose.exceptions import JWTError
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User, AccessLevel  # måste finnas i ditt projekt


router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()


# --- Schemas ---
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


# --- JWT helpers ---
def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(days=7)

    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
    }

    # VIKTIGT: signa med JWT_SECRET
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    settings = get_settings()
    try:
        # VIKTIGT: decoda med samma JWT_SECRET
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None


# --- Auth dependency ---
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = (credentials.credentials or "").strip()

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


# --- Routes ---
@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    # hitta user på email
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()

    # skapa user om den inte finns
    if not user:
        user = User(
            email=request.email,
            access_level=AccessLevel.FREE,  # eller vad du vill defaulta till
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(str(user.id), user.email)
    return LoginResponse(token=token, user_id=str(user.id), email=user.email)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    # access_level kan vara enum → gör str säkert
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
