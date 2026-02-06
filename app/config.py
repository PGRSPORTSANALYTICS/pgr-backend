from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Grund
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    debug: bool = False

    # DB
    database_url: str

    # Auth (detta är nyckeln vi använder överallt)
    jwt_secret: str

    # Server
    host: str = "0.0.0.0"
    port: int = 5000

    # Pydantic Settings (läser env vars + ev .env lokalt)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
