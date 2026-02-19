from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    environment: str = os.getenv("ENVIRONMENT", "production")  # production/dev
    frontend_url: str = os.getenv("FRONTEND_URL", "https://pgrsportsanalytics.com")
    backend_base_url: str = os.getenv("BACKEND_BASE_URL", "https://pgr-backend-production.up.railway.app")
    frontend_success_url: str = os.getenv("FRONTEND_SUCCESS_URL", "https://pgrsportsanalytics.com/premium/access/")

    # Discord OAuth
    discord_client_id: str | None = os.getenv("DISCORD_CLIENT_ID")
    discord_client_secret: str | None = os.getenv("DISCORD_CLIENT_SECRET")
    discord_redirect_uri: str = os.getenv("DISCORD_REDIRECT_URI", "")  # ex: https://<backend>/discord/callback

    # Discord bot for roles
    discord_bot_token: str | None = os.getenv("DISCORD_BOT_TOKEN")
    discord_guild_id: str | None = os.getenv("DISCORD_GUILD_ID")
    discord_premium_role_id: str | None = os.getenv("DISCORD_PREMIUM_ROLE_ID")

    # Stripe
    stripe_secret_key: str | None = os.getenv("STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    stripe_price_id: str | None = os.getenv("STRIPE_PRICE_ID")  # price_xxx

    # Cookie behavior
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"


def get_settings() -> Settings:
    return Settings()
