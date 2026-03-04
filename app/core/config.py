from typing import Optional

from dotenv import load_dotenv
from pydantic import EmailStr
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    database_url: str
    app_title: str = 'Speech_diarization'
    secret: str
    first_superuser_email: Optional[EmailStr] = None
    first_superuser_password: Optional[str] = None
    cookie_secure: bool = True
    allow_http_webhooks: bool = False
    allow_private_webhook_hosts: bool = False

    class Config:
        env_file = '.env'


settings = Settings()
