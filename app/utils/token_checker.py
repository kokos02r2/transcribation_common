import hashlib
import logging

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload  # Импортируем joinedload

from app.core.db import get_async_session
from app.models import APIToken

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    """Хеширует API-токен (должно совпадать с методом генерации)."""
    return hashlib.sha256(token.encode()).hexdigest()


async def validate_api_token(
    request: Request,  # Добавляем запрос для отладки заголовков
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    """Валидирует API-токен, сравнивая его хеш с базой данных."""

    # Логируем все заголовки запроса
    logger.info(f"Заголовки запроса: {request.headers}")

    if not authorization:
        raise HTTPException(status_code=401, detail="API-токен обязателен (нет заголовка Authorization)")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API-токен должен быть в формате 'Bearer <token>'")

    raw_token = authorization.split("Bearer ")[1]
    hashed_token = hash_token(raw_token)

    # Добавляем опцию joinedload для жадной загрузки пользователя
    query = select(APIToken).options(joinedload(APIToken.user)).where(APIToken.token == hashed_token)
    result = await session.execute(query)
    api_token = result.scalars().first()

    if not api_token:
        raise HTTPException(status_code=401, detail="Неверный API-токен")

    return api_token  # Возвращаем объект токена
