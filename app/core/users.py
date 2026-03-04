from typing import Optional, Union

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, InvalidPasswordException
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_session
from app.models.users import User
from app.schemas.users import UserCreate


# Подключение к базе данных
async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)

# Настройка транспорта через Cookie
cookie_transport = CookieTransport(
    cookie_name="auth_token",  # Имя cookie
    cookie_max_age=3600,       # Время жизни cookie в секундах (1 час)
    cookie_httponly=True,      # Делает cookie недоступным для JavaScript
    cookie_secure=settings.cookie_secure,
)


# Настройка JWT стратегии
def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.secret,
        lifetime_seconds=3600,  # Время жизни токена совпадает с cookie
    )


# Определение бэкенда авторизации
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=cookie_transport,  # Используем Cookie вместо Bearer
    get_strategy=get_jwt_strategy,
)


# Класс менеджера пользователей
class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    async def validate_password(
        self,
        password: str,
        user: Union[UserCreate, User],
    ) -> None:
        if len(password) < 3:
            raise InvalidPasswordException(
                reason="Password should be at least 3 characters"
            )
        if user.email in password:
            raise InvalidPasswordException(
                reason="Password should not contain e-mail"
            )

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ):
        print(f"Пользователь {user.email} зарегистрирован.")


# Зависимость для получения менеджера пользователей
async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)

# Инициализация FastAPI Users
fastapi_users = FastAPIUsers[User, int](
    get_user_manager,
    [auth_backend],
)

# Зависимости для текущего пользователя
current_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
