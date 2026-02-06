from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from jose.exceptions import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re

from app.config import get_settings
from app.database import get_db
from app.models.user import User, AccessLevel
from app.services.audit import audit_service

router = APIRouter(prefix="/auth", tags=["auth"])

# auto_error=False så vi kan göra egen robust token-extraktion
security = HTTPBearer(auto_error=False)

# --------- helpers ---------

_WHITESPACE_RE = re.compile(r"\s+")

def _clean_token(token: str) -> str:
    """
    Gör token "ren":
    - tar bort whitespace/radbrytningar
    - tar bort eventuella citattecken
    - tar bort 'Bearer ' eller 'bearer ' (och även 'Bearer Bearer ')
    """
    if not token:
        return ""

    t = token.strip().strip('"').strip("'")
    t = _WHITESPACE_RE.sub("", t)  # tar bort ALL whitespace

    # Om någon råkat skicka "Bearerxxx" utan space
    if t.lower().startswith("bearer"):
        # hantera både "Bearer<token>" och "Bearer <token>"
        t = t[6:]
        if t.startswith(":"):
            t = t[1:]
        t = t.lstrip()

    # Om det är "BearerBearertoken" pga swagger-dubbelprefix
    if t.lower().startswith("bearer"):
        t = t[6:].lstrip()

    # Om det är "Bearer.<token>" etc (säkerhet)
    t = t.lstrip(".")
    return t


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> str:
    """
    Tar token från:
    1) Swagger/HTTPBearer (credentials.credentials)
    2) Authorization-header manuellt (om swagger strular)
    3) X-Access-Token header (extra fallback)
    4) query param ?token= (debug-fallback)
    """
    token = ""

    if credentials and credentials.credentials:
        token = credentials.credentials

    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            # Kan vara "Bearer xxx" eller annat. Vi städar sen.
            token = auth_header

    if not token:
        token = request.headers.get("X-Access-Token", "")

    if not token:
        token = request.query_params.get("token", "")

    return _clean_token(token)


def _jwt_fingerprint(secret: str) -> str:
    # Liten fingerprint för logg/ /_debug (inte hela secreten)
    import hashlib
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def create_access_token(user_id: str, email: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=7)

    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }

    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    # Logga fingerprint bara (så vi vet att samma secret används)
    print(f"JWT SIGN fp={_jwt_fingerprint(settings.jwt_secret)}")
    return token


def decode_token(token: str) -> Optional[Dict]:
    settings = get_settings()
    try:
        print(f"JWT VERIFY fp={_jwt_fingerprint(settings.jwt_secret)} token_len={len(token)} dots={token.count('.')}")
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except Exception as e:
        print(f"JWT decode error: {repr(e)} token_len={len(token)} dots={token.count('.')}")
        return None


# --------- routes ---------

from pydantic import BaseModel, EmailStr

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


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token(request, credentials)

    # Om token saknar 2 punkter så är det inte ens en JWT
    if token.count(".") != 2:
        raise HTTPException(status_code=401, detail="Token is not a JWT (expected 3 segments)")

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


@router.get("/_debug")
async def debug_settings():
    s = get_settings()
    return {
        "jwt_fp": _jwt_fingerprint(s.jwt_secret),
        }
