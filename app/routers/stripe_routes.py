import os
import asyncio
from functools import partial
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from app.database import get_db, async_session_maker
from app.models.user import User, AccessLevel
from app.models.subscription import Subscription
from app.services.stripe_service import stripe_service
from app.services.audit import audit_service
from app.routers.auth import get_current_user
import uuid

router = APIRouter(prefix="/stripe", tags=["stripe"])

class CreateCheckoutRequest(BaseModel):
    price_id: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None

class CheckoutResponse(BaseModel):
    checkout_url: str

@router.post("/create-checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(
    request: CreateCheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    replit_domains = os.getenv("REPLIT_DOMAINS", "localhost:8000")
    base_url = f"https://{replit_domains.split(',')[0]}"
    
    success_url = request.success_url or f"{base_url}/checkout/success"
    cancel_url = request.cancel_url or f"{base_url}/checkout/cancel"
    
    customer_id = current_user.stripe_customer_id
    
    if not customer_id:
        loop = asyncio.get_event_loop()
        customer = await loop.run_in_executor(
            None, 
            lambda: asyncio.run(stripe_service.create_customer(current_user.email, current_user.id))
        )
        customer_id = customer.id
        current_user.stripe_customer_id = customer_id
        await db.commit()
    
    loop = asyncio.get_event_loop()
    session = await stripe_service.create_checkout_session(
        customer_id=customer_id,
        price_id=request.price_id,
        success_url=success_url,
        cancel_url=cancel_url
    )
    
    await audit_service.log(
        db=db,
        event_type="checkout_session_created",
        source="stripe",
        status="success",
        user_id=current_user.id
    )
    
    return CheckoutResponse(checkout_url=session.url)

@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")
    
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        await audit_service.log_standalone(
            event_type="webhook_error",
            source="stripe",
            status="error",
            details="STRIPE_WEBHOOK_SECRET not configured"
        )
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    
    try:
        event = await stripe_service.construct_webhook_event(payload, sig_header, webhook_secret)
    except Exception as e:
        await audit_service.log_standalone(
            event_type="webhook_signature_invalid",
            source="stripe",
            status="error",
            details=str(e)
        )
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})
    
    async with async_session_maker() as db:
        try:
            if event_type == "checkout.session.completed":
                customer_id = data.get("customer")
                subscription_id = data.get("subscription")
                
                result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
                user = result.scalar_one_or_none()
                
                if user:
                    sub_result = await db.execute(
                        select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
                    )
                    subscription = sub_result.scalar_one_or_none()
                    
                    if not subscription:
                        subscription = Subscription(
                            id=str(uuid.uuid4()),
                            user_id=user.id,
                            stripe_subscription_id=subscription_id,
                            stripe_customer_id=customer_id,
                            status="active"
                        )
                        db.add(subscription)
                    else:
                        subscription.status = "active"
                    
                    user.access_level = AccessLevel.PREMIUM
                    await db.commit()
                    
                    await audit_service.log(
                        db=db,
                        event_type="subscription_activated",
                        source="stripe",
                        status="success",
                        user_id=user.id
                    )
            
            elif event_type == "customer.subscription.updated":
                subscription_id = data.get("id")
                status = data.get("status")
                
                result = await db.execute(
                    select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
                )
                subscription = result.scalar_one_or_none()
                
                if subscription:
                    subscription.status = status
                    subscription.plan = data.get("items", {}).get("data", [{}])[0].get("price", {}).get("id")
                    
                    if status in ["canceled", "unpaid", "past_due"]:
                        user_result = await db.execute(select(User).where(User.id == subscription.user_id))
                        user = user_result.scalar_one_or_none()
                        if user:
                            user.access_level = AccessLevel.FREE
                    
                    await db.commit()
                    
                    await audit_service.log(
                        db=db,
                        event_type="subscription_updated",
                        source="stripe",
                        status="success",
                        user_id=subscription.user_id,
                        details=f"Status: {status}"
                    )
            
            elif event_type == "customer.subscription.deleted":
                subscription_id = data.get("id")
                
                result = await db.execute(
                    select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
                )
                subscription = result.scalar_one_or_none()
                
                if subscription:
                    subscription.status = "canceled"
                    
                    user_result = await db.execute(select(User).where(User.id == subscription.user_id))
                    user = user_result.scalar_one_or_none()
                    if user:
                        user.access_level = AccessLevel.FREE
                    
                    await db.commit()
                    
                    await audit_service.log(
                        db=db,
                        event_type="subscription_canceled",
                        source="stripe",
                        status="success",
                        user_id=subscription.user_id
                    )
        
        except Exception as e:
            await db.rollback()
            await audit_service.log_standalone(
                event_type="webhook_processing_error",
                source="stripe",
                status="error",
                details=str(e)
            )
            raise HTTPException(status_code=500, detail="Webhook processing error")
    
    return {"received": True}
