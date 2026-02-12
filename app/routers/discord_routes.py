# app/routers/discord_routes.py

from __future__ import annotations

import secrets
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_ME = "https://discord.com/api/users/@me"


# ---------------------------
# Helpers
# ---------------------------

def _build_discord_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    settings = get_settings()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        # VIKTIGT: email krävs om du vill skapa user via /start-flödet
        "scope": "identify email",
        "state": state,
        # "prompt": "consent",  # valfritt
    }
    return f"{DISCORD_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _decode_user_id_from_jwt_state(state_token: str) -> str:
    """
    Om du kör /discord/connect?token=<JWT> och skickar JWT vidare som state,
    så kan callback koppla rätt user_id utan Authorization-header.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            state_token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid state token")

    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="State token missing user id")

    return str(user_id)


async def _exchange_code_for_access_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(DISCORD_TOKEN_URL, data=data, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord token exchange failed: {resp.text}")

    token_json = resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Discord did not return access_token")

    return access_token


async def _fetch_discord_me(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(DISCORD_API_ME, headers=headers)

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord /users/@me failed: {resp.text}")

    return resp.json()


# ---------------------------
# Public start (för hemsidans knapp)
# ---------------------------

@router.get("/start")
async def discord_start(request: Request):
    """
    Publik start som hemsidan kan länka till.
    Skapar en CSRF-state (cookie) och redirectar till Discord.
    """
    settings = get_settings()

    if not settings.discord_client_id or not settings.discord_redirect_uri:
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    state = secrets.token_urlsafe(32)
    url = _build_discord_authorize_url(
        client_id=settings.discord_client_id,
        redirect_uri=settings.discord_redirect_uri,
        state=state,
    )

    resp = RedirectResponse(url)
    # Cookie för att verifiera att callback är legit (CSRF-skydd)
    resp.set_cookie(
        key="discord_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=10 * 60,  # 10 min
    )
    return resp


# ---------------------------
# Connect (om du vill koppla till redan inloggad user)
# ---------------------------

@router.get("/connect")
async def discord_connect(token: str):
    """
    Frontend skickar JWT som query:
      /discord/connect?token=<JWT>

    Vi skickar samma token vidare som OAuth 'state' så callback kan koppla rätt user.
    """
    settings = get_settings()

    if not token:
        raise HTTPException(status_code=400, detail="Missing token")

    if not settings.discord_client_id or not settings.discord_redirect_uri:
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify email",
        "state": token,  # <-- NYCKELN: vi tar med JWT här
    }

    url = f"{DISCORD_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


# ---------------------------
# Callback (Discord redirectar hit)
# ---------------------------

@router.get("/callback")
async def discord_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Discord skickar tillbaka:
      /discord/callback?code=...&state=...

    Vi verifierar state:
      - Om flow startade via /start => cookie_state måste matcha state
      - Om ingen cookie => state tolkas som JWT (från /connect)
    Sen:
      - code -> access_token
      - hämta Discord user (id + email)
      - uppdatera/skap user i DB
      - redirect till FRONTEND_BASE_URL/?discord=linked
    """
    settings = get_settings()

    if not (settings.discord_client_id and settings.discord_client_secret and settings.discord_redirect_uri):
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")

    # 1) Verifiera state
    cookie_state = request.cookies.get("discord_state")
    user_id_from_jwt: Optional[str] = None

    if cookie_state:
        # Start-flöde
        if cookie_state != state:
            raise HTTPException(status_code=401, detail="Invalid state")
    else:
        # Connect-flöde (JWT i state)
        user_id_from_jwt = _decode_user_id_from_jwt_state(state)

    # 2) code -> access_token
    access_token = await _exchange_code_for_access_token(
        code=code,
        client_id=settings.discord_client_id,
        client_secret=settings.discord_client_secret,
        redirect_uri=settings.discord_redirect_uri,
    )

    # 3) hämta Discord user (id + email)
    me = await _fetch_discord_me(access_token)
    discord_user_id = me.get("id")
    discord_email = me.get("email")  # <-- krävs för att slippa NULL email i DB

    if not discord_user_id:
        raise HTTPException(status_code=400, detail="Discord user id missing in response")

    # OBS: email kan vara None om scope saknar email
    if not discord_email:
        raise HTTPException(
            status_code=400,
            detail="Discord email missing (kontrollera att scope inkluderar 'email')",
        )

    # 4) Uppdatera DB
    if user_id_from_jwt:
        # Koppla Discord till en redan existerande user
        result = await db.execute(select(User).where(User.id == user_id_from_jwt))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found for state token")

        user.discord_user_id = str(discord_user_id)
        # (valfritt) synca email också
        user.email = user.email or discord_email

    else:
        # Start-flöde: skapa/uppdatera user via email
        result = await db.execute(select(User).where(User.email == discord_email))
        user = result.scalar_one_or_none()

        if not user:
            user = User(email=discord_email, access_level="free")
            db.add(user)

        user.discord_user_id = str(discord_user_id)

    await db.commit()

    # 5) Redirect till startsidan (A)
    done_url = "https://pgrsportsanalytics.com/discord/linked?success=1"
    resp = RedirectResponse(url=done_url)

    # städa state-cookie (om den fanns)
    resp.delete_cookie("discord_state")
    return resp
