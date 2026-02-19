from __future__ import annotations

import os
import secrets
import urllib.parse
import httpx

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/users/@me"


def _build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify email",
        "state": state,
        "prompt": "consent",
    }
    return DISCORD_AUTH_URL + "?" + urllib.parse.urlencode(params)


async def _exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(DISCORD_TOKEN_URL, data=data, headers=headers)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Discord token exchange failed: {r.text}")
    return r.json()["access_token"]


async def _fetch_discord_me(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(DISCORD_ME_URL, headers=headers)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Discord /me failed: {r.text}")
    return r.json()


@router.get("/start")
async def discord_start(request: Request):
    settings = get_settings()
    if not (settings.discord_client_id and settings.discord_client_secret and settings.discord_redirect_uri):
        raise HTTPException(status_code=500, detail="Discord env vars missing (CLIENT_ID/SECRET/REDIRECT_URI)")

    state = secrets.token_urlsafe(24)
    auth_url = _build_authorize_url(settings.discord_client_id, settings.discord_redirect_uri, state)

    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(
        key="discord_state",
        value=state,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=10 * 60,
    )
    return resp


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

    if not (settings.discord_client_id and settings.discord_client_secret and settings.discord_redirect_uri):
        raise HTTPException(status_code=500, detail="Discord env vars missing")

    access_token = await _exchange_code_for_token(
        code=code,
        client_id=settings.discord_client_id,
        client_secret=settings.discord_client_secret,
        redirect_uri=settings.discord_redirect_uri,
    )
    me = await _fetch_discord_me(access_token)

    discord_user_id = str(me.get("id") or "")
    discord_email = me.get("email")

    if not discord_user_id:
        raise HTTPException(status_code=400, detail="Discord user id missing")
    if not discord_email:
        raise HTTPException(status_code=400, detail="Discord email missing (ensure scope includes email)")

    result = await db.execute(select(User).where(User.email == discord_email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=discord_email, access_level="free")
        db.add(user)

    user.discord_user_id = discord_user_id
    await db.commit()

    done_url = f"{settings.frontend_url}/discord/linked?success=1"
    resp = RedirectResponse(url=done_url, status_code=302)

    resp.set_cookie(
        key="pgr_discord_id",
        value=discord_user_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )

    resp.delete_cookie("discord_state")
    return resp
