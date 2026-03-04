import secrets
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import WebhookToken
from datetime import datetime, timezone


class CRUDWebhookToken:
    def __init__(self, model):
        self.model = model

    async def get_by_user(self, user_id: int, session: AsyncSession):
        """Получить Webhook-токен пользователя"""
        result = await session.execute(
            select(self.model).where(self.model.user_id == user_id)
        )
        return result.scalars().first()

    async def update_or_create(self, user_id: int, session: AsyncSession):
        """Обновить существующий Webhook-токен или создать новый"""
        raw_token = secrets.token_hex(32)

        # Проверяем, существует ли токен
        existing_token = await self.get_by_user(user_id, session)
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)

        if existing_token:
            # Обновляем токен через UPDATE
            await session.execute(
                update(self.model)
                .where(self.model.user_id == user_id)
                .values(
                    token=raw_token,
                    created_at=current_time
                    )
            )
        else:
            # Создаем новый токен
            webhook_token = self.model(token=raw_token, user_id=user_id)
            session.add(webhook_token)

        await session.commit()  # Фиксируем изменения
        return {"webhook_token": raw_token}

    async def delete(self, user_id: int, session: AsyncSession):
        """Удалить Webhook-токен пользователя"""
        webhook_token = await self.get_by_user(user_id, session)
        if not webhook_token:
            return None
        await session.delete(webhook_token)
        await session.commit()  # Фиксируем удаление
        return webhook_token


# Экземпляр CRUD-класса
crud_webhook_token = CRUDWebhookToken(WebhookToken)
