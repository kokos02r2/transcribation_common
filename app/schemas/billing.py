from datetime import datetime

from pydantic import BaseModel
from typing import List, Optional


class AudioUsageRequest(BaseModel):
    start_date: datetime
    end_date: datetime


class AdminAudioUsageRequest(AudioUsageRequest):
    user_emails: Optional[List[str]] = None
