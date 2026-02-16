from typing import Optional
import logging

logger = logging.getLogger(__name__)

class AuditService:
    @staticmethod
    async def log(
        db=None,
        event_type: str = "",
        source: str = "",
        status: str = "",
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[str] = None
    ):
        logger.info(f"AUDIT | {event_type} | {source} | {status} | user={user_id} | {details or ''}")

    @staticmethod
    async def log_standalone(
        event_type: str = "",
        source: str = "",
        status: str = "",
        user_id: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[str] = None
    ):
        logger.info(f"AUDIT | {event_type} | {source} | {status} | user={user_id} | {details or ''}")

audit_service = AuditService()
