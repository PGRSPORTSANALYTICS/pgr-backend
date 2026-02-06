# app/routers/auth.py
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import hashlib
import uuid

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


def _secret_fingerprint() -> str:
    """
    Visar inte secreten, bara en kort fingerprint så vi kan bevisa
    att login och me använder samma key i logs.
    """
    secret = get_settings().jwt_secret.encode("utf-8")
    return hashlib.sha256(secret).hexdigest()[:10]


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=7)

    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": int(expire.timestamp()),
        "iat": int(datetime.now(timezone.utc).timestamp()),
    }

    token = jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")
    print("JWT SIGN fp=", _secret_fingerprint())
    return token


def decode_token(token: str) -> Optional[Dict]:
    try:
        print("JWT VERIFY fp=", _secret_fingerprint())
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except Exception as e:
        # Detta är guld i Railway Logs:
        print("JWT decode error:", repr(e))
        return None


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    # 1) Swagger HTTPBearer
    if credentials and credentials.credentials:
        t = credentials.credentials.strip()
    else:
        t = ""

    # 2) Header fallback
    if not t:
        auth = request.headers.get("Authorization", "").strip()
        if auth:
            parts = auth.split()
            if len(parts) >= 2 and parts[0].lower() == "bearer":
                t = " ".join(parts[1:]).strip()

    if not t:
        return None

    # Extra safety: om någon råkat få "Bearer Bearer ..."
    while t.lower().startswith("bearer "):
        t = t.split(" ", 1)[1].strip()

    return t or None


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


# Valfri: snabb “är secreten laddad?”-endpoint (kan tas bort sen)
@router.get("/_debug")
async def debug():
    return {"jwt_fp": _secret_fingerprint()}
