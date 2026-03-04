import datetime

from sqlalchemy import Column, DateTime, Integer, String, Boolean

from app.core.db import Base


class AudioLog(Base):
    __tablename__ = "audio_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_login = Column(String, index=True)
    file_name = Column(String, nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    task_id = Column(String, nullable=True)
    has_speech = Column(Boolean, nullable=True)
    processing_type = Column(String, nullable=True)
