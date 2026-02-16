from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 5000

    # DB
    database_url: str

    # Auth
    jwt_secret: str = ""

    @property
    def effective_jwt_secret(self) -> str:
        import os
        return self.jwt_secret or os.getenv("SESSION_SECRET", "dev-secret")

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
