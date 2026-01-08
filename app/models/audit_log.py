from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base
import uuid

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    source = Column(String(100), nullable=False)
    request_id = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
