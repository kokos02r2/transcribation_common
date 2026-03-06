from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_session
from app.core.users import current_user
from app.crud.token import crud_api_token
from app.crud.webhook_token import crud_webhook_token
from app.models import User

router = APIRouter()


@router.post("/generate_api_token/")
async def generate_api_token(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Генерация API-ключа (только один токен на пользователя)"""
    user_email = user.email
    token_data = await crud_api_token.create_or_update(user.id, session)
    return {
        "user_email": user_email,
        "api_token": token_data["api_token"]
    }


@router.delete("/delete_api_token/")
async def delete_api_token(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Удаление API-ключа (пользователь может удалить только свой токен)"""
    api_token = await crud_api_token.get_by_user(user.id, session)
    if not api_token:
        raise HTTPException(status_code=404, detail="API-токен не найден")
    await crud_api_token.delete(api_token, session)
    return {"message": "API-токен успешно удалён"}


@router.post("/generate_webhook_token/")
async def generate_webhook_token(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Генерация Webhook-токена (только один токен на пользователя)"""
    user_email = user.email
    token_data = await crud_webhook_token.update_or_create(user.id, session)
    return {
        "user_email": user_email,
        "webhook_token": token_data["webhook_token"],
    }


@router.delete("/delete_webhook_token/")
async def delete_webhook_token(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Удаление Webhook-токена (пользователь может удалить только свой токен)"""

    webhook_token = await crud_webhook_token.get_by_user(user.id, session)
    if not webhook_token:
        raise HTTPException(status_code=404, detail="Webhook-токен не найден")

    await crud_webhook_token.delete(user.id, session)

    return {"message": "Webhook-токен успешно удалён"}


@router.get("/get_webhook_token/")
async def get_webhook_token(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Получение текущего Webhook-токена пользователя"""
    webhook_token = await crud_webhook_token.get_by_user(user.id, session)
    if not webhook_token:
        raise HTTPException(status_code=404, detail="Webhook-токен не найден")
    return {
        "user_email": user.email,
        "webhook_token": webhook_token.token  # Предполагается, что у модели есть поле webhook_token
    }


@router.get("/get_api_token_status/")
async def get_api_token_status(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
):
    """Получение статуса API-токена без выдачи его значения."""
    api_token = await crud_api_token.get_by_user(user.id, session)
    if not api_token:
        return {
            "user_email": user.email,
            "exists": False,
            "created_at": None,
        }
    return {
        "user_email": user.email,
        "exists": True,
        "created_at": api_token.created_at.isoformat() if api_token.created_at else None,
    }
