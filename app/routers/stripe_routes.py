from __future__ import annotations

import json
import httpx
import stripe

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User, AccessLevel

router = APIRouter(prefix="/stripe", tags=["stripe"])


async def _grant_discord_role(discord_user_id: str):
    settings = get_settings()
    if not (settings.discord_bot_token and settings.discord_guild_id and settings.discord_premium_role_id):
        raise RuntimeError("Discord env vars missing (DISCORD_BOT_TOKEN / DISCORD_GUILD_ID / DISCORD_PREMIUM_ROLE_ID)")

    url = f"https://discord.com/api/v10/guilds/{settings.discord_guild_id}/members/{discord_user_id}/roles/{settings.discord_premium_role_id}"
    headers = {"Authorization": f"Bot {settings.discord_bot_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.put(url, headers=headers)

    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord grant failed: {r.status_code} {r.text}")


async def _revoke_discord_role(discord_user_id: str):
    settings = get_settings()
    if not (settings.discord_bot_token and settings.discord_guild_id and settings.discord_premium_role_id):
        raise RuntimeError("Discord env vars missing (DISCORD_BOT_TOKEN / DISCORD_GUILD_ID / DISCORD_PREMIUM_ROLE_ID)")

    url = f"https://discord.com/api/v10/guilds/{settings.discord_guild_id}/members/{discord_user_id}/roles/{settings.discord_premium_role_id}"
    headers = {"Authorization": f"Bot {settings.discord_bot_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.delete(url, headers=headers)

    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord revoke failed: {r.status_code} {r.text}")


@router.get("/checkout")
async def stripe_checkout(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY missing")
    if not settings.stripe_price_399:
        raise HTTPException(status_code=500, detail="STRIPE_PRICE_399 missing")

    stripe.api_key = settings.stripe_secret_key

    discord_id = request.cookies.get("pgr_discord_id")
    if not discord_id:
        return RedirectResponse(url=f"{settings.backend_base_url}/discord/start", status_code=302)

    result = await db.execute(select(User).where(User.discord_user_id == str(discord_id)))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=f"{discord_id}@discord.local", discord_user_id=str(discord_id), access_level= AccessLevel.free)
        db.add(user)
        await db.commit()

    success_url = f"{settings.frontend_success_url}"
    cancel_url = f"{settings.frontend_base_url}/cancel"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.stripe_price_399, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=str(discord_id),
            metadata={"discord_id": str(discord_id), "plan": "premium_399"},
            subscription_data={"metadata": {"discord_id": str(discord_id), "plan": "premium_399"}},
            allow_promotion_codes=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")

    return RedirectResponse(url=session.url, status_code=303)


async def _set_user_premium(db: AsyncSession, discord_id: str, stripe_customer_id: str | None):
    result = await db.execute(select(User).where(User.discord_user_id == str(discord_id)))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=f"{discord_id}@discord.local", discord_user_id=str(discord_id), access_level= AccessLevel.premium)
        db.add(user)
    user.access_level = AccessLevel.premium
    if stripe_customer_id:
        user.stripe_customer_id = stripe_customer_id
    await db.commit()


async def _set_user_free(db: AsyncSession, discord_id: str):
    result = await db.execute(select(User).where(User.discord_user_id == str(discord_id)))
    user = result.scalar_one_or_none()
    if not user:
        return
    user.access_level = AccessLevel.free
    await db.commit()


@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET missing")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.stripe_webhook_secret,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook signature error: {str(e)}")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        discord_id = data.get("client_reference_id") or (data.get("metadata") or {}).get("discord_id")
        stripe_customer_id = data.get("customer")

        if not discord_id:
            return {"ok": True, "note": "No discord_id in checkout.session.completed"}

        await _set_user_premium(db, str(discord_id), stripe_customer_id)

        try:
            await _grant_discord_role(str(discord_id))
        except Exception as e:
            print(f"[DISCORD_GRANT_ERROR] discord_id={discord_id} err={e}")
            return {"ok": True, "note": "Discord grant failed, check logs"}

        return {"ok": True, "granted": True, "discord_id": str(discord_id)}

    if event_type == "customer.subscription.deleted":
        meta = data.get("metadata") or {}
        discord_id = meta.get("discord_id")
        if discord_id:
            await _set_user_free(db, str(discord_id))
            try:
                await _revoke_discord_role(str(discord_id))
            except Exception as e:
                print(f"[DISCORD_REVOKE_ERROR] discord_id={discord_id} err={e}")
        return {"ok": True}

    return {"ok": True}
