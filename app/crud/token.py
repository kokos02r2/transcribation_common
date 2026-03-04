import hashlib
import secrets
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import APIToken
from datetime import datetime, timezone


def hash_token(token: str) -> str:
    """Функция хеширования токена"""
    return hashlib.sha256(token.encode()).hexdigest()


class CRUDAPIToken:
    def __init__(self, model):
        self.model = model

    async def get_by_user(self, user_id: int, session: AsyncSession):
        """Получить API-токен пользователя"""
        result = await session.execute(
            select(self.model).where(self.model.user_id == user_id)
        )
        return result.scalars().first()

    async def get_by_token(self, hashed_token: str, session: AsyncSession):
        """Найти API-токен по его хешу"""
        result = await session.execute(
            select(self.model).where(self.model.token == hashed_token)
        )
        return result.scalars().first()

    async def create_or_update(self, user_id: int, session: AsyncSession):
        """
        Создать или обновить API-токен для пользователя.
        Если токен уже существует – обновляем его значение,
        иначе – создаём новый.
        """
        existing_token = await self.get_by_user(user_id, session)
        # Генерация нового "сырых" и хешированного токена
        raw_token = secrets.token_hex(32)
        hashed_token = hash_token(raw_token)
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)

        if existing_token:
            # Обновляем существующий токен с новым хешем
            await session.execute(
                update(self.model)
                .where(self.model.user_id == user_id)
                .values(
                    token=hashed_token,
                    created_at=current_time
                )
            )
        else:
            # Создаем новый API-токен
            new_token = self.model(token=hashed_token, user_id=user_id)
            session.add(new_token)

        await session.commit()
        return {"api_token": raw_token, "hashed_token": hashed_token}

    async def delete(self, token: APIToken, session: AsyncSession):
        """Удалить API-токен"""
        await session.delete(token)
        await session.commit()


# Экземпляр CRUD‑класса
crud_api_token = CRUDAPIToken(APIToken)
