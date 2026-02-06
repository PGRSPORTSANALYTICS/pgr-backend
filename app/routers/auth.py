# app/routers/auth.py
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from jose import jwt
from jose.exceptions import JWTError

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import get_settings
from app.database import get_db
from app.models.user import User, AccessLevel
from app.services.audit import audit_service

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


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

    expire = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "sub": str(user_id),
        "email": email,
        # jose gillar säkrast unix timestamp (int)
        "exp": int(expire.timestamp()),
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception as e:
        # Loggar i Railway logs så vi kan se exakt fel
        print("JWT decode error:", repr(e))
        return None


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    """
    Stödjer:
    - Swagger HTTPBearer (credentials.credentials)
    - manuellt Authorization header
    - råkar vara 'Bearer Bearer <token>'
    - råkar klistra in 'Bearer <token>' i rutan
    """
    token = None

    if credentials and credentials.credentials:
        token = credentials.credentials.strip()

    if not token:
        auth = request.headers.get("Authorization", "").strip()
        if auth:
            # t.ex "Bearer xxx" eller "Bearer Bearer xxx"
            parts = auth.split()
            if len(parts) >= 2 and parts[0].lower() == "bearer":
                token = " ".join(parts[1:]).strip()

    if not token:
        return None

    # Om token råkar börja med "Bearer " igen (pga Bearer Bearer)
    while token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    return token if token else None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> User:
    token = _extract_token(request, credentials)

    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

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
            id=str(__import__("uuid").uuid4()),
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
        else str(current
