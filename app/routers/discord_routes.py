from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt
import httpx
import urllib.parse

from app.config import get_settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/discord", tags=["discord"])

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_ME = "https://discord.com/api/users/@me"


def _get_user_id_from_state(state_token: str) -> str:
    """
    state_token = din vanliga JWT från login (som du skickar in via /discord/connect?token=...)
    Vi decodar den här för att få user_id utan Authorization-header.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            state_token,
            settings.jwt_secret,                 # se till att detta finns i din config
            algorithms=[settings.jwt_algorithm], # t.ex. "HS256"
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid state token")

    # Stöd både "sub" och "user_id"
    user_id = payload.get("sub") or payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="State token missing user id")

    return str(user_id)


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
        "scope": "identify",
        "state": token,  # <-- NYCKELN: vi tar med JWT här
        # valfritt:
        # "prompt": "consent",
    }

    url = f"{DISCORD_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@router.get("/callback")
async def discord_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Discord skickar tillbaka: ?code=...&state=...
    Ingen Authorization-header finns här -> därför använder vi state (JWT) för user_id.
    """
    settings = get_settings()

    if not (settings.discord_client_id and settings.discord_client_secret and settings.discord_redirect_uri):
        raise HTTPException(status_code=500, detail="Discord env vars saknas")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")

    # 1) hitta user_id via state-token (JWT)
    user_id = _get_user_id_from_state(state)

    # 2) byt code -> access_token
    token_data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.discord_redirect_uri,
    }
    token_headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(DISCORD_TOKEN_URL, data=token_data, headers=token_headers)

    if token_resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord token exchange failed: {token_resp.text}")

    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Discord did not return access_token")

    # 3) hämta Discord user
    async with httpx.AsyncClient(timeout=20) as client:
        me_resp = await client.get(
            DISCORD_API_ME,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if me_resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"Discord /users/@me failed: {me_resp.text}")

    me = me_resp.json()
    discord_user_id = me.get("id")
    if not discord_user_id:
        raise HTTPException(status_code=400, detail="Discord user id missing in response")

    # 4) uppdatera DB: koppla discord_user_id till rätt user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found for state token")

    user.discord_user_id = str(discord_user_id)
    await db.commit()

    # 5) redirecta tillbaka till frontend (valfritt)
    # Sätt i env/config: FRONTEND_BASE_URL (t.ex. https://pgrsportsanalytics.com)
    if getattr(settings, "frontend_base_url", None):
        done_url = f"{settings.frontend_base_url}/discord/linked?success=1"
        return RedirectResponse(done_url)

    return JSONResponse({"status": "ok", "user_id": str(user_id), "discord_user_id": str(discord_user_id)})
