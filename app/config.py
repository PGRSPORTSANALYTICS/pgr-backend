import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"

    # DB
    database_url: str = os.getenv("DATABASE_URL", "")
    sqlalchemy_database_url: str = os.getenv("SQLALCHEMY_DATABASE_URL", "")

    # AUTH (DENNA är det viktiga)
    jwt_secret: str = os.getenv("JWT_SECRET", "")

    # Stripe (valfritt här, men bra att ha)
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    host: str = "0.0.0.0"
    port: int = int(os.getenv("PORT", "5000"))

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_secret:
        # Hellre fail-fast än att skapa tokens som alltid blir "Invalid token"
        raise RuntimeError("JWT_SECRET is missing. Set it in Railway -> pgr-backend -> Variables.")
    return s
