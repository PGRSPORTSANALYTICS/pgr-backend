from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    debug: bool = False

    # DB
    database_url: str

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"   # <-- LÄGG TILL

    # Discord
    discord_client_id: str | None = None          # <-- LÄGG TILL
    discord_client_secret: str | None = None      # <-- LÄGG TILL
    discord_redirect_uri: str | None = None       # <-- LÄGG TILL

    # Frontend (valfritt)
    frontend_base_url: str | None = None          # <-- valfritt

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
