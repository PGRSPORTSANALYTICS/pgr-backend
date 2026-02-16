from __future__ import annotations

import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

DATABASE_URL = os.getenv("DATABASE_URL", "")

def convert_database_url(url: str) -> str:
    parsed = urlparse(url)
    new_scheme = "postgresql+asyncpg"
    query_params = parse_qs(parsed.query)
    query_params.pop('sslmode', None)
    new_query = urlencode(query_params, doseq=True)
    new_parsed = parsed._replace(scheme=new_scheme, query=new_query)
    return urlunparse(new_parsed)

database_url = convert_database_url(DATABASE_URL)

engine = create_async_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"ssl": True} if "neon" in DATABASE_URL else {},
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
