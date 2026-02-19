from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import get_db
from app.config import get_settings
from app.database import init_db
from app.routers import health_router, discord_router, stripe_router


settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(
    title="PGR Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# Viktigt: allow_credentials=True för cookies (discord_id cookie)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(discord_router)
app.include_router(stripe_router)

