import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    environment: str = os.getenv("ENVIRONMENT", "production")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # DB
    database_url: str = os.getenv("DATABASE_URL", "")
    sqlalchemy_database_url: str = os.getenv("SQLALCHEMY_DATABASE_URL", "")

    # Secrets
    session_secret: str = os.getenv("SESSION_SECRET", "")

    # ✅ JWT_SECRET är den vi använder för JWT
    # fallback till SESSION_SECRET så allt inte dör om du råkat ha bara den
    jwt_secret: str = os.getenv("JWT_SECRET") or os.getenv("SESSION_SECRET") or "dev-insecure-secret"

    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
