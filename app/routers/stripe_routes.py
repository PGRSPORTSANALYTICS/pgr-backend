from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, AnyHttpUrl
import stripe

from app.config import get_settings, Settings

router = APIRouter(prefix="/stripe", tags=["stripe"])


class CreateCheckoutSessionIn(BaseModel):
    price_id: str
    success_url: AnyHttpUrl
    cancel_url: AnyHttpUrl


@router.post("/create-checkout-session")
async def create_checkout_session(
    payload: CreateCheckoutSessionIn,
    settings: Settings = Depends(get_settings),
):
    # Stripe kräver secret key (sk_...)
    if not settings.stripe_secret_key or not settings.stripe_secret_key.startswith("sk_"):
        raise HTTPException(status_code=500, detail="Stripe secret key missing (STRIPE_SECRET_KEY)")

    stripe.api_key = settings.stripe_secret_key

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": payload.price_id, "quantity": 1}],
            success_url=str(payload.success_url) + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=str(payload.cancel_url),
            allow_promotion_codes=True,
        )
        return {"checkout_url": session.url, "id": session.id}

    except stripe.error.StripeError as e:
        # Stripe-fel (price id fel, fel konto, test/live mismatch etc)
        raise HTTPException(status_code=400, detail=f"StripeError: {getattr(e, 'user_message', str(e))}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ServerError: {str(e)}")


@router.post("/webhook")
async def stripe_webhook(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.stripe_webhook_secret or not settings.stripe_webhook_secret.startswith("whsec_"):
        raise HTTPException(status_code=500, detail="Stripe webhook secret missing (STRIPE_WEBHOOK_SECRET)")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=settings.stripe_webhook_secret,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook signature verify failed: {str(e)}")

    # Lägg logik här senare (uppgradera user access etc)
    return {"received": True, "type": event["type"]}
