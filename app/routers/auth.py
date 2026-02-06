from fastapi import APIRouter, Depends, HTTPException, Request
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

# Auto_error=False så vi kan ge egen 401 och även stödja raw header om swagger strular
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
    expire = datetime.utcnow() + timedelta(days=7)

    to_encode = {
        "sub": user_id,
        "email": email,
        "exp": expire,
    }

    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[Dict]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception:
        return None


def _extract_token_from_anywhere(
    credentials: Optional[HTTPAuthorizationCredentials],
    request: Request,
) -> Optional[str]:
    # 1) Standard (Swagger/HTTPBearer): credentials.credentials är redan "eyJ..."
    if credentials and credentials.credentials:
        token = credentials.credentials.strip()
    else:
        token = ""

    # 2) Fallback: ta från Authorization header (om credentials inte triggar)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            parts = auth_header.strip().split()
            # Viktigt: tar sista delen så "Bearer Bearer eyJ..." blir eyJ...
            token = parts[-1] if parts else ""

    # 3) Om någon råkat klistra in "Bearer eyJ..." som tokenvärde:
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    return token or None


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token_from_anywhere(credentials, request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except JWTError as e:
        # Ger tydlig text (bra för debug)
        raise HTTPException(status_code=401, detail=f"JWT decode failed: {e.__class__.__name__}")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

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
