from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    debug: bool = False

    # DB
    database_url: str

    # Auth (ENDA vi ska använda för JWT)
    jwt_secret: str

    # Server
    host: str = "0.0.0.0"
    port: int = 5000

    # Pydantic settings
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    s = Settings()
    # Fail fast om JWT_SECRET saknas
    if not s.jwt_secret or len(s.jwt_secret) < 20:
        raise RuntimeError("JWT_SECRET missing/too short. Set JWT_SECRET in Railway variables.")
    return s
