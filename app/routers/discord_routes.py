import os
import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.auth import get_current_user  # om du har denna
from app.models.user import User  # justera om filnamnet skiljer

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

DISCORD_API_BASE = "https://discord.com/api"


@router.get("/connect")
def discord_connect(current_user: User = Depends(get_current_user)):
    if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    scope = "identify"
    url = (
        f"{DISCORD_API_BASE}/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return RedirectResponse(url)


@router.get("/callback")
def discord_callback(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI):
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    # 1) Byt code -> token
    token_resp = requests.post(
        f"{DISCORD_API_BASE}/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_resp.text}")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token from Discord")

    # 2) Hämta Discord user
    me_resp = requests.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )

    if me_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Fetch /users/@me failed: {me_resp.text}")

    discord_id = me_resp.json().get("id")
    if not discord_id:
        raise HTTPException(status_code=400, detail="No discord id returned")

    # 3) Spara på user
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.discord_user_id = str(discord_id)
    db.commit()

    return {"ok": True, "discord_user_id": user.discord_user_id}
