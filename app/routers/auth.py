from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr

from app.database import get_db
from app.models.user import User, AccessLevel
from app.config import get_settings
from app.services.audit import audit_service

router = APIRouter(prefix="/auth", tags=["auth"])

# Swagger "Authorize" (auto_error=False så vi kan acceptera token från andra ställen också)
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


def _clean_token(token: Optional[str]) -> str:
    if not token:
        return ""
    t = token.strip().strip('"').strip("'").strip()
    if t.lower().startswith("bearer "):
        t = t.split(" ", 1)[1].strip()
    return t


def _looks_like_jwt(token: str) -> bool:
    # JWT = 3 segment med två punkter
    return token.count(".") == 2 and len(token) > 20


def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()
    expire = datetime.utcnow() + timedelta(days=7)

    to_encode = {
        "sub": user_id,
        "email": email,
        "exp": expire,
    }

    return jwt.encode(to_encode, settings.effective_jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.effective_jwt_secret, algorithms=["HS256"])
    except Exception:
        return None


def _extract_token_anywhere(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
    token_query: Optional[str],
    x_access_token: Optional[str],
) -> str:
    # 1) Query param: /auth/me?token=...
    token = _clean_token(token_query)
    if token:
        return token

    # 2) Header: X-Access-Token: ...
    token = _clean_token(x_access_token)
    if token:
        return token

    # 3) Swagger/HTTPBearer: credentials.credentials
    if credentials and credentials.credentials:
        token = _clean_token(credentials.credentials)
        if token:
            return token

    # 4) Raw Authorization header fallback
    auth_header = request.headers.get("Authorization", "")
    token = _clean_token(auth_header)
    return token


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Query(default=None, description="JWT token (fallback om du inte vill använda Swagger Authorize)"),
    x_access_token: Optional[str] = Header(default=None, alias="X-Access-Token"),
) -> User:
    raw = _extract_token_anywhere(request, credentials, token, x_access_token)

    if not raw:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    if not _looks_like_jwt(raw):
        # Detta fångar “not enough segments” innan jose ens försöker decoda
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        payload = jwt.decode(raw, get_settings().jwt_secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

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

    return LoginResponse(token=token, user_id=user.id, email=user.email)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
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
