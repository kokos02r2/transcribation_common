from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from sqlalchemy.orm import relationship

from app.core.db import Base


class User(SQLAlchemyBaseUserTable[int], Base):
    api_tokens = relationship("APIToken", back_populates="user", cascade="all, delete-orphan")
    webhook_tokens = relationship("WebhookToken", back_populates="user", cascade="all, delete-orphan")
