from __future__ import annotations
from enum import Enum
from sqlalchemy import Enum as SAEnum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)

class AccessLevel(str, Enum):
    free = "free"
    premium = "premium"
    
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    discord_user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    access_level: Mapped[AccessLevel] = mapped_column(SAEnum(AccessLevel, name="accesslevel"), default=AccessLevel.free, nullable=False)  # free/premium
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
