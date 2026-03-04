from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.db import Base


class WebhookToken(Base):
    __tablename__ = "webhook_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    token = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="webhook_tokens")
