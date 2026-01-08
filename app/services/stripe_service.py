import os
import httpx
import stripe
from typing import Optional, Any

class StripeService:
    def __init__(self):
        self._stripe_client: Optional[stripe.StripeClient] = None
        self._publishable_key: Optional[str] = None
        self._secret_key: Optional[str] = None
    
    async def _get_credentials(self) -> dict:
        hostname = os.getenv("REPLIT_CONNECTORS_HOSTNAME")
        repl_identity = os.getenv("REPL_IDENTITY")
        web_repl_renewal = os.getenv("WEB_REPL_RENEWAL")
        
        if repl_identity:
            x_replit_token = f"repl {repl_identity}"
        elif web_repl_renewal:
            x_replit_token = f"depl {web_repl_renewal}"
        else:
            raise ValueError("X_REPLIT_TOKEN not found for repl/depl")
        
        is_production = os.getenv("REPLIT_DEPLOYMENT") == "1"
        target_environment = "production" if is_production else "development"
        
        url = f"https://{hostname}/api/v2/connection"
        params = {
            "include_secrets": "true",
            "connector_names": "stripe",
            "environment": target_environment
        }
        headers = {
            "Accept": "application/json",
            "X_REPLIT_TOKEN": x_replit_token
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            data = response.json()
        
        items = data.get("items", [])
        if not items:
            raise ValueError(f"Stripe {target_environment} connection not found")
        
        connection_settings = items[0].get("settings", {})
        publishable = connection_settings.get("publishable")
        secret = connection_settings.get("secret")
        
        if not publishable or not secret:
            raise ValueError(f"Stripe {target_environment} credentials incomplete")
        
        return {"publishable_key": publishable, "secret_key": secret}
    
    async def get_stripe_client(self) -> stripe.StripeClient:
        if not self._secret_key:
            creds = await self._get_credentials()
            self._secret_key = creds["secret_key"]
            self._publishable_key = creds["publishable_key"]
        
        stripe.api_key = self._secret_key
        return stripe
    
    async def get_publishable_key(self) -> str:
        if not self._publishable_key:
            creds = await self._get_credentials()
            self._publishable_key = creds["publishable_key"]
        return self._publishable_key
    
    async def create_customer(self, email: str, user_id: str) -> Any:
        client = await self.get_stripe_client()
        return client.Customer.create(
            email=email,
            metadata={"user_id": user_id}
        )
    
    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str
    ) -> Any:
        client = await self.get_stripe_client()
        return client.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url
        )
    
    async def construct_webhook_event(self, payload: bytes, sig_header: str, webhook_secret: str) -> Any:
        client = await self.get_stripe_client()
        return client.Webhook.construct_event(payload, sig_header, webhook_secret)

stripe_service = StripeService()
