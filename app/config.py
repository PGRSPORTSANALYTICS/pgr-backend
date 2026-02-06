import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"

    # tidigare saker som ditt projekt redan använder
    environment: str = os.getenv("REPLIT_DEPLOYMENT", "production")
    debug: bool = os.getenv("REPLIT_DEPLOYMENT", "0") == "1"

    database_url: str = os.getenv("DATABASE_URL", "")
    sqlalchemy_database_url: str = os.getenv("SQLALCHEMY_DATABASE_URL", "")

    # BEHÅLL DENNA (annars kraschar andra delar av projektet)
    session_secret: str = os.getenv("SESSION_SECRET", "")

    # NY: JWT_SECRET (men vi låter den falla tillbaka på SESSION_SECRET)
    jwt_secret: str = os.getenv("JWT_SECRET", "")  # kan vara tom

    host: str = "0.0.0.0"
    port: int = int(os.getenv("PORT", "5000"))

    replit_domains: str = os.getenv("REPLIT_DOMAINS", "")

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    s = Settings()
    # Om JWT_SECRET saknas, använd SESSION_SECRET så auth ändå funkar
    if not s.jwt_secret:
        s.jwt_secret = s.session_secret
    return s
