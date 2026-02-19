# app/routers/stripe_routes.py
# Production-safe Stripe webhook handler:
# - Verifies signature
# - Handles checkout.session.completed (captures discord_id/plan, can grant immediately)
# - Handles invoice.paid (SOURCE OF TRUTH for paid access)
# - Handles customer.subscription.updated / deleted (downgrade/revoke)
# - Idempotency via DB event log (optional but strongly recommended)

from __future__ import annotations

import os
from typing import Any, Optional, Dict

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

router = APIRouter()

# ---- configure stripe ----
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# ---- your deps / functions (you already have these) ----
# from app.db import get_db
# from app.services.users import _set_user_premium, _set_user_free
# from app.services.discord import _grant_discord_role, _revoke_discord_role
# from app.services.subscriptions import _create_or_update_subscription, _cancel_subscription

async def get_db() -> AsyncSession:  # placeholder – replace with your actual Depends(get_db)
    raise NotImplementedError


# =========================
# Helpers
# =========================

def _safe_get_meta(obj: Any) -> Dict[str, str]:
    meta = getattr(obj, "metadata", None) or (obj.get("metadata") if isinstance(obj, dict) else None) or {}
    # ensure keys/values are strings
    out: Dict[str, str] = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[str(k)] = str(v)
    return out


def _pick_discord_id(*candidates: Optional[str]) -> Optional[str]:
    for c in candidates:
        if c and str(c).strip():
            return str(c).strip()
    return None


async def _event_already_processed(db: AsyncSession, event_id: str) -> bool:
    """
    Idempotency guard. Create a small table if you don't have one:

    CREATE TABLE IF NOT EXISTS stripe_events (
      id TEXT PRIMARY KEY,
      created_at TIMESTAMPTZ DEFAULT now()
    );
    """
    # If you don't want event log yet, return False always.
    try:
        res = await db.execute(text("SELECT 1 FROM stripe_events WHERE id = :id LIMIT 1"), {"id": event_id})
        return res.scalar_one_or_none() is not None
    except Exception:
        # table not created yet -> no idempotency
        return False


async def _mark_event_processed(db: AsyncSession, event_id: str) -> None:
    try:
        await db.execute(
            text("INSERT INTO stripe_events (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"),
            {"id": event_id},
        )
        await db.commit()
    except Exception:
        # table not created yet -> ignore
        pass


async def _ensure_subscription_row(
    db: AsyncSession,
    *,
    user_id: str,
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str],
    plan: Optional[str],
    status: str,
    current_period_end: Optional[int] = None,
) -> None:
    """
    Minimal DB upsert so 'subscriptions' table won't stay empty.
    Adjust column names to your schema if needed.
    Assumed columns (from your screenshot):
      user_id, stripe_subscription_id, stripe_customer_id, plan, status, current_period_end
    """
    await db.execute(
        text(
            """
            INSERT INTO subscriptions (user_id, stripe_subscription_id, stripe_customer_id, plan, status, current_period_end)
            VALUES (:user_id, :sub_id, :cust_id, :plan, :status, :cpe)
            ON CONFLICT (user_id) DO UPDATE SET
              stripe_subscription_id = COALESCE(EXCLUDED.stripe_subscription_id, subscriptions.stripe_subscription_id),
              stripe_customer_id     = COALESCE(EXCLUDED.stripe_customer_id,     subscriptions.stripe_customer_id),
              plan                   = COALESCE(EXCLUDED.plan,                   subscriptions.plan),
              status                 = EXCLUDED.status,
              current_period_end     = COALESCE(EXCLUDED.current_period_end,     subscriptions.current_period_end)
            """
        ),
        {
            "user_id": user_id,
            "sub_id": stripe_subscription_id,
            "cust_id": stripe_customer_id,
            "plan": plan,
            "status": status,
            "cpe": current_period_end,
        },
    )
    await db.commit()


async def _activate_access(
    db: AsyncSession,
    *,
    discord_id: str,
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str],
    plan: Optional[str],
    current_period_end: Optional[int] = None,
) -> None:
    # 1) Set user premium in your users table
    await _set_user_premium(db, str(discord_id), stripe_customer_id)

    # 2) Keep subscriptions table in sync
    await _ensure_subscription_row(
        db,
        user_id=str(discord_id),
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        plan=plan,
        status="active",
        current_period_end=current_period_end,
    )

    # 3) Grant role (don’t fail webhook if Discord fails)
    try:
        await _grant_discord_role(str(discord_id))
    except Exception as e:
        print("[DISCORD_GRANT_ERROR]", discord_id, str(e))


async def _deactivate_access(
    db: AsyncSession,
    *,
    discord_id: str,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> None:
    await _set_user_free(db, str(discord_id))

    # update subscription row (if exists)
    try:
        await _ensure_subscription_row(
            db,
            user_id=str(discord_id),
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            plan=None,
            status="canceled",
            current_period_end=None,
        )
    except Exception as e:
        print("[SUBSCRIPTION_UPDATE_ERROR]", discord_id, str(e))

    try:
        await _revoke_discord_role(str(discord_id))
    except Exception as e:
        print("[DISCORD_REVOKE_ERROR]", discord_id, str(e))


# =========================
# Webhook Route
# =========================

@router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET missing")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook signature error: {str(e)}")

    event_id = event.get("id")
    event_type = event.get("type")
    data_obj = event.get("data", {}).get("object", {})

    # Idempotency (recommended)
    if event_id and await _event_already_processed(db, event_id):
        return {"ok": True, "deduped": True}

    try:
        # -------------------------
        # 1) CHECKOUT COMPLETED
        # -------------------------
        if event_type == "checkout.session.completed":
            session = data_obj
            meta = _safe_get_meta(session)

            # discord id might come from client_reference_id OR metadata.discord_id
            discord_id = _pick_discord_id(
                session.get("client_reference_id"),
                meta.get("discord_id"),
            )

            # Helpful fields
            stripe_customer_id = session.get("customer")
            # If it’s a subscription checkout, Stripe sets subscription id on the session:
            stripe_subscription_id = session.get("subscription")
            plan = meta.get("plan")

            # If no discord id, we cannot map to a user — return ok (don’t fail Stripe)
            if not discord_id:
                print("[WEBHOOK] checkout.session.completed missing discord_id", {"event_id": event_id})
                if event_id:
                    await _mark_event_processed(db, event_id)
                return {"ok": True, "note": "No discord_id in checkout.session.completed"}

            # ✅ Optional: you can grant immediately here (some people want instant)
            # BUT SOURCE-OF-TRUTH should still be invoice.paid.
            await _activate_access(
                db,
                discord_id=discord_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                plan=plan,
                current_period_end=None,
            )

            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "handled": event_type, "discord_id": discord_id}

        # -------------------------
        # 2) INVOICE PAID (best for access)
        # -------------------------
        elif event_type == "invoice.paid":
            invoice = data_obj
            meta = _safe_get_meta(invoice)

            # invoice metadata is usually the most reliable place
            discord_id = _pick_discord_id(meta.get("discord_id"))
            stripe_customer_id = invoice.get("customer")
            stripe_subscription_id = invoice.get("subscription")
            plan = meta.get("plan")

            # current_period_end can be derived from lines or subscription object; invoice has period_end sometimes
            current_period_end = invoice.get("period_end")  # may be None
            if not current_period_end:
                # try: invoice.lines.data[0].period.end
                try:
                    lines = invoice.get("lines", {}).get("data", []) or []
                    if lines and lines[0].get("period", {}).get("end"):
                        current_period_end = int(lines[0]["period"]["end"])
                except Exception:
                    pass

            if not discord_id:
                print("[WEBHOOK] invoice.paid missing discord_id", {"event_id": event_id})
                if event_id:
                    await _mark_event_processed(db, event_id)
                return {"ok": True, "note": "No discord_id in invoice.paid"}

            await _activate_access(
                db,
                discord_id=discord_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
                plan=plan,
                current_period_end=current_period_end,
            )

            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "handled": event_type, "discord_id": discord_id}

        # -------------------------
        # 3) SUBSCRIPTION UPDATED
        # -------------------------
        elif event_type == "customer.subscription.updated":
            sub = data_obj
            meta = _safe_get_meta(sub)

            stripe_customer_id = sub.get("customer")
            stripe_subscription_id = sub.get("id")
            status = sub.get("status")  # active, past_due, canceled, unpaid, etc.
            current_period_end = sub.get("current_period_end")

            # Some implementations store discord_id in subscription metadata
            discord_id = _pick_discord_id(meta.get("discord_id"))

            # If not present, you can look it up by stripe_customer_id in your DB if you store it on user
            if not discord_id and stripe_customer_id:
                try:
                    # Example: users table has stripe_customer_id + discord_id (adjust to your schema)
                    res = await db.execute(
                        text("SELECT discord_id FROM users WHERE stripe_customer_id = :cid LIMIT 1"),
                        {"cid": stripe_customer_id},
                    )
                    discord_id = res.scalar_one_or_none()
                    if discord_id:
                        discord_id = str(discord_id)
                except Exception:
                    pass

            if not discord_id:
                print("[WEBHOOK] subscription.updated missing discord_id", {"event_id": event_id})
                if event_id:
                    await _mark_event_processed(db, event_id)
                return {"ok": True, "note": "No discord_id in customer.subscription.updated"}

            # Decide access from status
            if status in ("active", "trialing"):
                await _activate_access(
                    db,
                    discord_id=discord_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    plan=meta.get("plan"),
                    current_period_end=current_period_end,
                )
            elif status in ("canceled", "unpaid"):
                await _deactivate_access(
                    db,
                    discord_id=discord_id,
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                )
            else:
                # past_due / incomplete etc: keep subscription row but don’t necessarily revoke instantly
                await _ensure_subscription_row(
                    db,
                    user_id=str(discord_id),
                    stripe_customer_id=stripe_customer_id,
                    stripe_subscription_id=stripe_subscription_id,
                    plan=meta.get("plan"),
                    status=status or "unknown",
                    current_period_end=current_period_end,
                )

            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "handled": event_type, "discord_id": discord_id, "status": status}

        # -------------------------
        # 4) SUBSCRIPTION DELETED (revoke)
        # -------------------------
        elif event_type == "customer.subscription.deleted":
            sub = data_obj
            meta = _safe_get_meta(sub)

            stripe_customer_id = sub.get("customer")
            stripe_subscription_id = sub.get("id")
            discord_id = _pick_discord_id(meta.get("discord_id"))

            if not discord_id and stripe_customer_id:
                try:
                    res = await db.execute(
                        text("SELECT discord_id FROM users WHERE stripe_customer_id = :cid LIMIT 1"),
                        {"cid": stripe_customer_id},
                    )
                    discord_id = res.scalar_one_or_none()
                    if discord_id:
                        discord_id = str(discord_id)
                except Exception:
                    pass

            if not discord_id:
                print("[WEBHOOK] subscription.deleted missing discord_id", {"event_id": event_id})
                if event_id:
                    await _mark_event_processed(db, event_id)
                return {"ok": True, "note": "No discord_id in customer.subscription.deleted"}

            await _deactivate_access(
                db,
                discord_id=discord_id,
                stripe_customer_id=stripe_customer_id,
                stripe_subscription_id=stripe_subscription_id,
            )

            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "handled": event_type, "discord_id": discord_id}

        # -------------------------
        # 5) OPTIONAL: INVOICE PAYMENT FAILED
        # -------------------------
        elif event_type == "invoice.payment_failed":
            invoice = data_obj
            meta = _safe_get_meta(invoice)
            discord_id = _pick_discord_id(meta.get("discord_id"))
            stripe_customer_id = invoice.get("customer")
            stripe_subscription_id = invoice.get("subscription")

            # usually do NOT instantly revoke; mark status for visibility
            if discord_id:
                try:
                    await _ensure_subscription_row(
                        db,
                        user_id=str(discord_id),
                        stripe_customer_id=stripe_customer_id,
                        stripe_subscription_id=stripe_subscription_id,
                        plan=meta.get("plan"),
                        status="past_due",
                        current_period_end=None,
                    )
                except Exception as e:
                    print("[SUBSCRIPTION_UPDATE_ERROR]", str(e))

            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "handled": event_type}

        # -------------------------
        # Unhandled events
        # -------------------------
        else:
            if event_id:
                await _mark_event_processed(db, event_id)
            return {"ok": True, "ignored": event_type}

    except HTTPException:
        raise
    except Exception as e:
        # Don't let unexpected exceptions cause Stripe retries forever without visibility
        print("[WEBHOOK_FATAL_ERROR]", event_type, event_id, str(e))
        raise HTTPException(status_code=500, detail="Webhook handler error")


# =========================
# You must provide these in your project
# =========================

async def _set_user_premium(db: AsyncSession, discord_id: str, stripe_customer_id: Optional[str]) -> None:
    """
    Implement your existing logic.
    Should set users.access_level = premium and store stripe_customer_id if you want lookup later.
    """
    await db.execute(
        text(
            """
            UPDATE users
            SET access_level = 'premium',
                stripe_customer_id = COALESCE(:cid, stripe_customer_id)
            WHERE discord_id = :did
            """
        ),
        {"did": discord_id, "cid": stripe_customer_id},
    )
    await db.commit()


async def _set_user_free(db: AsyncSession, discord_id: str) -> None:
    await db.execute(
        text("UPDATE users SET access_level = 'free' WHERE discord_id = :did"),
        {"did": discord_id},
    )
    await db.commit()


async def _grant_discord_role(discord_id: str) -> None:
    """
    Call your discord bot/service to grant premium role.
    """
    # implement in your codebase
    return


async def _revoke_discord_role(discord_id: str) -> None:
    """
    Call your discord bot/service to revoke premium role.
    """
    # implement in your codebase
    return
