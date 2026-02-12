from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
import urllib.parse
import secrets

from app.config import get_settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_ME = "https://discord.com/api/users/@me"


# --------------------------
# Helpers
# --------------------------

def _build_discord_authorize_url(settings, state: str) -> str:
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify email",
        "state": state,
    }
    return f"{DISCORD_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def _exchange_code_for_access_token(code: str, settings) -> str:
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.discord_redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(DISCORD_TOKEN_URL, data=data, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")

    token_json = resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token returned")

    return access_token


async def _fetch_discord_user(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(DISCORD_API_ME, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord /users/@me failed: {resp.text}")

    user_json = resp.json()
    if not user_json.get("id"):
        raise HTTPException(status_code=400, detail="Missing Discord user id")

    return user_json


# --------------------------
# Start flow (hemsidans knapp)
# --------------------------

@router.get("/start")
async def discord_start(request: Request):
    settings = get_settings()

    if not settings.discord_client_id or not settings.discord_redirect_uri:
        raise HTTPException(status_code=500, detail="Discord config missing (client_id/redirect_uri)")

    state = secrets.token_urlsafe(32)
    url = _build_discord_authorize_url(settings, state)  # ✅ FIX: rätt funktionsnamn

    response = RedirectResponse(url)
    response.set_cookie(
        "discord_state",
        state,
        httponly=True,
        secure=True,       # rekommenderas i prod (https)
        samesite="lax",
        max_age=600,
    )
    return response


# --------------------------
# Callback från Discord
# --------------------------

@router.get("/callback")
async def discord_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()

    cookie_state = request.cookies.get("discord_state")
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=401, detail="Invalid state")

    access_token = await _exchange_code_for_access_token(code, settings)
    discord_user = await _fetch_discord_user(access_token)

    discord_user_id = str(discord_user["id"])
    discord_email = discord_user.get("email")

    # Om Discord inte lämnar email så kan du antingen:
    # - kräva att användaren har verified email på Discord
    # - eller fallback till "username@discord.local" (inte rekommenderat)
    if not discord_email:
        raise HTTPException(status_code=400, detail="Discord did not return email (is it verified?)")

    # --------------------------
    # Hitta eller skapa user
    # --------------------------
    result = await db.execute(select(User).where(User.email == discord_email))
    user = result.scalar_one_or_none()

    if not user:
        user = User(email=discord_email, access_level="free")
        db.add(user)
        await db.flush()

    user.discord_user_id = discord_user_id
    await db.commit()

    # städa state-cookie
    resp = None
    if settings.frontend_base_url:
        resp = RedirectResponse(f"{settings.frontend_base_url}/discord/linked?success=1")
    else:
        resp = JSONResponse({"status": "ok", "email": discord_email, "discord_user_id": discord_user_id})

    resp.delete_cookie("discord_state")
    return resp
