import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    environment: str = os.getenv("ENVIRONMENT", "production")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # DB (stöd både DATABASE_URL och SQLALCHEMY_DATABASE_URL)
    database_url: str = (
        os.getenv("SQLALCHEMY_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    )

    # Secrets
    session_secret: str = os.getenv("SESSION_SECRET", "dev-session-secret-change-me")
    jwt_secret: str = os.getenv("JWT_SECRET", "")

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    # Pydantic settings config
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
