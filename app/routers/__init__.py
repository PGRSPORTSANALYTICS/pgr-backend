from app.routers.health_routes import router as health_router
from app.routers.discord_routes import router as discord_router
from app.routers.stripe_routes import router as stripe_router

__all__ = ["health_router", "discord_router", "stripe_router"]
