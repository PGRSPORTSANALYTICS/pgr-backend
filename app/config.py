import os
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    app_name: str = "PGR Backend"
    app_version: str = "1.0.0"
    environment: str = os.getenv("REPLIT_DEPLOYMENT", "development")
    debug: bool = os.getenv("REPLIT_DEPLOYMENT", "0") != "1"
    
    database_url: str = os.getenv("DATABASE_URL", "")
    session_secret: str = os.getenv("SESSION_SECRET", "dev-secret-key")
    
    host: str = "0.0.0.0"
    port: int = int(os.getenv("PORT", "5000"))
    
    replit_domains: str = os.getenv("REPLIT_DOMAINS", "")
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
