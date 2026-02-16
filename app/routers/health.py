from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.database import get_db
from app.config import get_settings

router = APIRouter(tags=["core"])

@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {"status": "ok", "db": db_status}

@router.get("/version")
async def version():
    settings = get_settings()
    return {
        "version": "1.0.0",
        "environment": settings.environment,
        "app_name": "PGR Backend"
    }
