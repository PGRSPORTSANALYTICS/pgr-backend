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


# -------------------------
# Helpers
# -------------------------

def _build_discord_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        # "prompt": "consent",  # valfritt
    }
    return f"{DISCORD_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _decode_user_id_from_jwt_state(state_token: str) -> str:
    """
    Om du vill använda /discord/connect?token=<JWT> och skicka den som state:
    Då kan callback koppla rätt user_id med hjälp av JWT.
    (Detta behövs inte för public /discord/start)
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

    # Stöd både "sub" och "user_id"
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


async def _fetch_discord_user_id(access_token: str) -> str:
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        me_resp = await client.get(DISCORD_API_ME, headers=headers)

    if me_resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord /users/@me failed: {me_resp.text}")

    me_json = me_resp.json()
    discord_user_id = me_json.get("id")
    if not discord_user_id:
        raise HTTPException(status_code=400, detail="Discord user id missing in response")

    return str(discord_user_id)


# -------------------------
# Public start (för hemsidans knapp)
# -------------------------

@router.get("/start")
async def discord_start(request: Request):
    """
    Publik start som hemsidan kan länka till.
    Skapar CSRF-state och redirectar till Discord.
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

    resp = RedirectResponse(url=url)

    # Spara state i cookie så callback kan verifiera att det är samma session
    resp.set_cookie(
        key="discord_state",
        value=state,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=10 * 60,  # 10 minuter
    )
    return resp


# -------------------------
# Optional: Inloggad start (om du redan har JWT i frontend)
# -------------------------

@router.get("/connect")
async def discord_connect(token: str):
    """
    Inloggad variant: frontend skickar JWT som query:
    /discord/connect?token=<JWT>
    Vi skickar token vidare som Discord "state".
    """
    settings = get_settings()

    if not token:
        raise HTTPException(status_code=400, detail="Missing token")

    if not settings.discord_client_id or not settings.discord_redirect_uri:
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    url = _build_discord_authorize_url(
        client_id=settings.discord_client_id,
        redirect_uri=settings.discord_redirect_uri,
        state=token,
    )
    return RedirectResponse(url=url)


# -------------------------
# Callback (Discord redirectar hit)
# -------------------------

@router.get("/callback")
async def discord_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Discord skickar tillbaka: /discord/callback?code=...&state=...
    Vi verifierar state (cookie för /start) eller tolkar state som JWT (för /connect),
    byter code->access_token, hämtar discord_user_id, sparar i DB, redirectar till frontend.
    """
    settings = get_settings()

    if not (settings.discord_client_id and settings.discord_client_secret and settings.discord_redirect_uri):
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")

    # 1) Verifiera state:
    #    - Om flow startade via /start => cookie_state måste matcha
    #    - Om flow startade via /connect => state är JWT (ingen cookie krävs)
    cookie_state = request.cookies.get("discord_state")
    user_id_from_jwt: Optional[str] = None

    if cookie_state:
        # Vi kom troligen från /start
        if cookie_state != state:
            raise HTTPException(status_code=401, detail="Invalid state")
    else:
        # Ingen cookie => troligen /connect med JWT i state
        # Om du inte vill stödja /connect kan du ta bort detta block.
        user_id_from_jwt = _decode_user_id_from_jwt_state(state)

    # 2) code -> access_token
    access_token = await _exchange_code_for_access_token(
        code=code,
        client_id=settings.discord_client_id,
        client_secret=settings.discord_client_secret,
        redirect_uri=settings.discord_redirect_uri,
    )

    # 3) hämta discord user id
    discord_user_id = await _fetch_discord_user_id(access_token)

    # 4) Uppdatera DB
    #    A) Om vi har user_id_from_jwt => koppla discord_user_id till den användaren
    #    B) Annars (public flow) => hitta/ skapa user på discord_user_id
    if user_id_from_jwt:
        result = await db.execute(select(User).where(User.id == user_id_from_jwt))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found for state token")

        user.discord_user_id = discord_user_id
        await db.commit()
    else:
        # Public flow: skapa eller hitta på discord_user_id
        result = await db.execute(select(User).where(User.discord_user_id == discord_user_id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                discord_user_id=discord_user_id,
                access_level="free",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            # finns redan, säkerställ att den i alla fall är kopplad
            if not user.discord_user_id:
                user.discord_user_id = discord_user_id
                await db.commit()

    # 5) Redirect till frontend
    # Ex: https://pgrsportsanalytics.com/discord/linked?success=1
    base = getattr(settings, "frontend_base_url", None) or "https://pgrsportsanalytics.com"
    done_url = f"{base}/discord/linked?success=1"

    # Rensa state-cookie (så den inte ligger kvar)
    resp = RedirectResponse(url=done_url)
    resp.delete_cookie("discord_state")
    return resp
