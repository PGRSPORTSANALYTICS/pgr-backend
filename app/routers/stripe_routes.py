from __future__ import annotations

from typing import Any, Dict, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings

router = APIRouter(prefix="/stripe", tags=["stripe"])


async def _set_user_premium(db: AsyncSession, discord_user_id: str):
    await db.execute(
        text("UPDATE users SET access_level='premium' WHERE discord_user_id=:id"),
        {"id": discord_user_id},
    )
    await db.commit()


async def _set_user_free(db: AsyncSession, discord_id: str):
    await db.execute(
        text("UPDATE users SET access_level='free' WHERE discord_user_id=:id"),
        {"id": discord_user_id},
    )
    await db.commit()


async def _grant_discord_role(discord_user_id: str):
    # TODO: koppla din riktiga Discord-grant här
    return True


async def _revoke_discord_role(discord_user_id: str):
    # TODO: koppla riktig revoke här
    return True
    
# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _require_settings(settings):
    if not getattr(settings, "stripe_secret_key", None):
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY missing")
    if not getattr(settings, "stripe_webhook_secret", None):
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET missing")
    if not getattr(settings, "stripe_price_id", None):
        raise HTTPException(status_code=500, detail="STRIPE_PRICE_ID missing")
    if not getattr(settings, "frontend_url", None):
        raise HTTPException(status_code=500, detail="FRONTEND_URL missing")


async def _idempotency_check(db: AsyncSession, event_id: str) -> bool:
    try:
        await db.execute(
            text("INSERT INTO stripe_events (id) VALUES (:id)"),
            {"id": event_id},
        )
        await db.commit()
        return False
    except Exception as e:
        print("WEBHOOK SIGNATURE ERROR:", repr(e))
           raise HTTPException(status_code=400, detail=f"Webhook signature error:{str(e)}")
        await db.rollback()
        return True


def _extract_discord_id(obj: Dict[str, Any]) -> Optional[str]:
    if not obj:
        return None

    if obj.get("client_reference_id"):
        return str(obj["client_reference_id"])

    meta = obj.get("metadata") or {}
    if meta.get("discord_id"):
        return str(meta["discord_id"])

    sub_details = obj.get("subscription_details") or {}
    meta2 = sub_details.get("metadata") or {}
    if meta2.get("discord_id"):
        return str(meta2["discord_id"])

    return None


def _extract_plan(obj: Dict[str, Any]) -> Optional[str]:
    meta = obj.get("metadata") or {}
    return str(meta.get("plan")) if meta.get("plan") else None


# --------------------------------------------------
# Checkout
# --------------------------------------------------
from fastapi.responses import RedirectResponse

@router.get("/checkout")
async def checkout_get(request: Request):
    settings = get_settings()
    _require_settings(settings)
    stripe.api_key = settings.stripe_secret_key

    # Hämta discord_id från cookie (om du redan sätter den vid login)
    discord_id = request.cookies.get("discord_id")
    plan = request.query_params.get("plan", "premium_399")

    if not discord_id:
        raise HTTPException(status_code=400, detail="discord_id cookie missing")

    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        client_reference_id=str(discord_id),
        success_url=f"{settings.frontend_success_url}/?success=true",
        cancel_url=f"{settings.frontend_url}/cancel",
        metadata={"discord_id": str(discord_id), "plan": str(plan)},
        subscription_data={"metadata": {"discord_id": str(discord_id), "plan": str(plan)}},
        allow_promotion_codes=True,
    )

    return RedirectResponse(session.url, status_code=303)

@router.post("/checkout")
async def create_checkout_session(request: Request):
    settings = get_settings()
    _require_settings(settings)

    stripe.api_key = settings.stripe_secret_key

    payload = await request.json()

    discord_id = payload.get("discord_id")   # 👈 LÄS FRÅN BODY
    plan = payload.get("plan", "premium_399")

    if not discord_id:
        raise HTTPException(status_code=400, detail="discord_id required")

    session = stripe.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        client_reference_id=str(discord_id),
        success_url=f"{settings.frontend_url}/premium/access",
        cancel_url=f"{settings.frontend_url}/cancel",
        metadata={
            "discord_id": str(discord_id),
            "plan": str(plan),
        },
        subscription_data={
            "metadata": {
                "discord_id": str(discord_id),
                "plan": str(plan),
            }
        },
        allow_promotion_codes=True,
    )

    return {"checkout_url": session.url}

# --------------------------------------------------
# Customer Portal
# --------------------------------------------------

@router.post("/portal")
async def create_customer_portal(request: Request):
    settings = get_settings()
    _require_settings(settings)

    stripe.api_key = settings.stripe_secret_key

    payload = await request.json()
    customer_id = payload.get("stripe_customer_id")

    if not customer_id:
        raise HTTPException(status_code=400, detail="stripe_customer_id required")

    session = stripe.billing_portal.Session.create(
        customer=str(customer_id),
        return_url=f"{settings.frontend_url}/account",
    )

    return {"portal_url": session.url}


# --------------------------------------------------
# Webhook
# --------------------------------------------------

@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    _require_settings(settings)

    stripe.api_key = settings.stripe_secret_key

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

    event_id = event.get("id")
    if event_id:
        already = await _idempotency_check(db, event_id)
        if already:
            return {"ok": True, "idempotent": True}

    event_type = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    # --------------------------------------------------
    # 1️⃣ Checkout completed
    # --------------------------------------------------
    if event_type == "checkout.session.completed":
        discord_id = _extract_discord_id(obj)
        stripe_customer_id = obj.get("customer")
        plan = _extract_plan(obj) or "premium_399"

        if not discord_id:
            return {"ok": True, "note": "No discord_id in checkout"}

        await _set_user_premium(db, str(discord_id))

        try:
            await _grant_discord_role(str(discord_id))
            return {"ok": True, "granted": True}
        except Exception as e:
            return {"ok": True, "granted": False, "error": str(e)}

    # --------------------------------------------------
    # 2️⃣ Invoice paid (renewal)
    # --------------------------------------------------
    if event_type in ("invoice.paid", "invoice.payment_succeeded"):
        stripe_customer_id = obj.get("customer")
        discord_user_id = _extract_discord_id(obj)
        subscription_id = obj.get("subscription")
        plan = None

        if not discord_user_id and subscription_id:
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
                discord_user_id = _extract_discord_id(sub)
                
            except Exception:
                pass

        if not discord_user_id:
            return {"ok": True, "note": "Invoice paid but no discord_user_id"}

        await _set_user_premium(db, str(discord_user_id))

        try:
            await _grant_discord_role(str(discord_user_id))
        except Exception:
            pass

        return {"ok": True, "invoice": True}

    # --------------------------------------------------
    # 3️⃣ Subscription updated / deleted
    # --------------------------------------------------
    if event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
        discord_id = _extract_discord_id(obj)
        stripe_customer_id = obj.get("customer")
        status = obj.get("status")

        if not discord_user_id:
            return {"ok": True}

        if event_type == "customer.subscription.deleted" or status in ("canceled", "unpaid"):
            await _set_user_free(db, str(discord_user_id))
            try:
                await _revoke_discord_role(str(discord_user_id))
            except Exception:
                pass
            return {"ok": True, "revoked": True}

        await _set_user_premium(db, str(discord_user_id), str(stripe_customer_id), access_level=_extract_plan(obj) or "premium_399")

        try:
            await _grant_discord_role(str(discord_user_id))
        except Exception:
            pass

        return {"ok": True, "updated": True}

    return {"ok": True, "ignored": event_type}
