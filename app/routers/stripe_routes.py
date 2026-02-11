# app/routers/stripe_routes.py

import os
import json
import stripe
import requests
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/stripe", tags=["stripe"])

# ==== ENV ====
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_399 = os.getenv("STRIPE_PAYMENT_399", "")  # <- price_xxx från Stripe (399 kr)

SUCCESS_URL_DEFAULT = os.getenv("STRIPE_SUCCESS_URL", "https://pgrsportsanalytics.com/success")
CANCEL_URL_DEFAULT = os.getenv("STRIPE_CANCEL_URL", "https://pgrsportsanalytics.com/cancel")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_PREMIUM_ROLE_ID = os.getenv("DISCORD_PREMIUM_ROLE_ID", "")

stripe.api_key = STRIPE_SECRET_KEY


# ==== HELPERS (Discord Role Grant) ====
def _discord_headers():
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def grant_premium_role(discord_user_id: str) -> None:
    """
    Adds PREMIUM role to a Discord user in a guild.
    Bot needs: Manage Roles + role must be below bot's top role.
    """
    if not (DISCORD_BOT_TOKEN and DISCORD_GUILD_ID and DISCORD_PREMIUM_ROLE_ID):
        raise RuntimeError("Discord env vars missing (DISCORD_BOT_TOKEN / DISCORD_GUILD_ID / DISCORD_PREMIUM_ROLE_ID)")

    url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{DISCORD_PREMIUM_ROLE_ID}"
    r = requests.put(url, headers=_discord_headers(), timeout=15)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord grant failed: {r.status_code} {r.text}")


def revoke_premium_role(discord_user_id: str) -> None:
    if not (DISCORD_BOT_TOKEN and DISCORD_GUILD_ID and DISCORD_PREMIUM_ROLE_ID):
        raise RuntimeError("Discord env vars missing (DISCORD_BOT_TOKEN / DISCORD_GUILD_ID / DISCORD_PREMIUM_ROLE_ID)")

    url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{DISCORD_PREMIUM_ROLE_ID}"
    r = requests.delete(url, headers=_discord_headers(), timeout=15)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord revoke failed: {r.status_code} {r.text}")


# ==== REQUEST MODELS ====
class CreateCheckoutBody(BaseModel):
    discord_id: str
    success_url: str | None = None
    cancel_url: str | None = None


# ==== ROUTES ====
@router.post("/create-checkout-session")
async def create_checkout_session(body: CreateCheckoutBody):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY missing")
    if not STRIPE_PRICE_399:
        raise HTTPException(status_code=500, detail="STRIPE_PAYMENT_399 missing (should be Stripe price_xxx)")
    if not body.discord_id:
        raise HTTPException(status_code=400, detail="discord_id is required")

    success_url = body.success_url or SUCCESS_URL_DEFAULT
    cancel_url = body.cancel_url or CANCEL_URL_DEFAULT

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_399, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=body.discord_id,  # <- superviktigt (kopplar till discord)
            metadata={
                "discord_id": body.discord_id,
                "plan": "premium_399",
            },
            subscription_data={
                "metadata": {
                    "discord_id": body.discord_id,
                    "plan": "premium_399",
                }
            },
            allow_promotion_codes=True,
        )
        return {"checkout_url": session.url, "id": session.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET missing")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook signature error: {str(e)}")

    event_type = event["type"]
    data = event["data"]["object"]

    # 1) När checkout är klar -> ge roll direkt
    if event_type == "checkout.session.completed":
        # Discord-id kan ligga i client_reference_id eller metadata
        discord_id = data.get("client_reference_id") or (data.get("metadata") or {}).get("discord_id")
        if not discord_id:
            # Om inget discord_id finns kan vi inte koppla – men webhook ska ändå svara 200 så Stripe inte retryar i evighet
            return {"ok": True, "note": "No discord_id found on checkout.session.completed"}

        try:
            grant_premium_role(discord_id)
        except Exception as e:
            # Svara 200 ändå (så du slipper Stripe retry-storm), men logga felet i Railway
            print(f"[DISCORD_GRANT_ERROR] discord_id={discord_id} err={e}")
            return {"ok": True, "note": "Discord grant failed, check logs"}

        return {"ok": True, "granted": True, "discord_id": discord_id}

    # 2) Om subscription avslutas -> ta bort roll (valfritt men rekommenderat)
    if event_type == "customer.subscription.deleted":
        sub = data
        discord_id = (sub.get("metadata") or {}).get("discord_id")
        if discord_id:
            try:
                revoke_premium_role(discord_id)
            except Exception as e:
                print(f"[DISCORD_REVOKE_ERROR] discord_id={discord_id} err={e}")
        return {"ok": True}

    # 3) Ignorera resten (men svara 200)
    return {"ok": True, "type": event_type}
