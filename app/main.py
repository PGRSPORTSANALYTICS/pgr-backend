from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

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
    swagger_ui_parameters={"persistAuthorization": True},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(discord_router)
app.include_router(stripe_router)

@app.get("/")
async def root():
    return {
        "message": "PGR Backend API",
        "version": "1.0.0",
        "environment": settings.environment
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.environment != "production"
    )
