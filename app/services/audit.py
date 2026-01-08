from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit_log import AuditLog
from app.database import async_session_maker
import uuid

class AuditService:
    @staticmethod
    async def log(
        db: AsyncSession,
        event_type: str,
        source: str,
        status: str,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[str] = None
    ) -> AuditLog:
        log_entry = AuditLog(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type=event_type,
            source=source,
            status=status,
            request_id=request_id,
            details=details
        )
        db.add(log_entry)
        await db.commit()
        await db.refresh(log_entry)
        return log_entry
    
    @staticmethod
    async def log_standalone(
        event_type: str,
        source: str,
        status: str,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[str] = None
    ) -> Optional[AuditLog]:
        try:
            async with async_session_maker() as session:
                log_entry = AuditLog(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    event_type=event_type,
                    source=source,
                    status=status,
                    request_id=request_id,
                    details=details
                )
                session.add(log_entry)
                await session.commit()
                await session.refresh(log_entry)
                return log_entry
        except Exception:
            return None

audit_service = AuditService()
