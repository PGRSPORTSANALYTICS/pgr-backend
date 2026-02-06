from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from jose.exceptions import JWTError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User, AccessLevel

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()


# -------------------------
# Schemas
# -------------------------
class LoginRequest(BaseModel):
    email: str  # håller str för att slippa email-validator dependency


class LoginResponse(BaseModel):
    token: str
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: str
    email: str
    access_level: str
    created_at: Optional[str]  # isoformat


# -------------------------
# JWT helpers
# -------------------------
def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        # detta är config-fel i Railway, inte användarens fel
        raise HTTPException(status_code=500, detail="JWT_SECRET not set on server")
    return secret


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(days=7)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except JWTError:
        return None
    except Exception:
        return None


# -------------------------
# Auth dependency (diagnostic)
# -------------------------
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = (credentials.credentials or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    # 1) decode
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"JWT decode failed: {type(e).__name__}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"JWT decode failed: {type(e).__name__}")

    # 2) validate payload
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing sub")

    # 3) DB lookup (UUID-safe)
    try:
        user_id_value = uuid.UUID(str(user_id))
    except Exception:
        user_id_value = str(user_id)

    result = await db.execute(select(User).where(User.id == user_id_value))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found for sub")

    return user


# -------------------------
# Routes
# -------------------------
@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    # enkel email sanity-check
    if "@" not in request.email:
        raise HTTPException(status_code=422, detail="Invalid email")

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

    return LoginResponse(
        token=token,
        user_id=str(user.id),
        email=user.email,
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    access_level = (
        current_user.access_level.value
        if hasattr(current_user.access_level, "value")
        else str(current_user.access_level)
    )

    created = None
    if getattr(current_user, "created_at", None):
        try:
            created = current_user.created_at.isoformat()
        except Exception:
            created = str(current_user.created_at)

    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        access_level=access_level,
        created_at=created,
    )
