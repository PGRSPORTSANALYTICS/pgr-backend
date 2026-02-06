# app/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Grund
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    debug: bool = False

    # DB (måste finnas som env)
    database_url: str

    # Auth (måste finnas som env)
    jwt_secret: str

    # Server (Railway kör PORT)
    host: str = "0.0.0.0"
    port: int = 5000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
