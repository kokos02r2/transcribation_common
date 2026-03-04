import os
import redis
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.sql import func
from app.core.db import get_async_session
from app.core.users import current_superuser, current_user
from app.models.audiolog import AudioLog
from app.schemas.billing import AdminAudioUsageRequest, AudioUsageRequest
from sqlalchemy import case
from app.models import User

load_dotenv()
router = APIRouter()

TEMP_FOLDER = "temporary_files"
REDIS_URL = os.getenv("REDIS_URL")
os.makedirs(TEMP_FOLDER, exist_ok=True)

redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


@router.post("/audio_usage/")
async def get_audio_usage(
    request_data: AudioUsageRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user)
):
    """
    Получает общее время использования аудиофайлов в минутах за указанный период + разбивку по дням.
    Принимает JSON с параметрами start_date, end_date.
    Считает минуты с речью и без, предполагая, что NULL в has_speech означает True.
    Учитывает processing_type для минут с речью с разбивкой по дням.
    """
    try:
        query = (
            select(
                func.date(AudioLog.created_at).label("date"),
                AudioLog.processing_type.label("processing_type"),
                func.sum(AudioLog.duration_seconds).label("total_seconds"),
                func.sum(
                    case(
                        (AudioLog.has_speech.is_(False), 0),
                        else_=AudioLog.duration_seconds
                    )
                ).label("speech_seconds"),
                func.sum(
                    case(
                        (AudioLog.has_speech.is_(False), AudioLog.duration_seconds),
                        else_=0
                    )
                ).label("no_speech_seconds")
            )
            .where(AudioLog.user_login == user.email)
            .where(AudioLog.created_at >= request_data.start_date)
            .where(AudioLog.created_at < request_data.end_date)
            .group_by(func.date(AudioLog.created_at), AudioLog.processing_type)
            .order_by(func.date(AudioLog.created_at))
        )

        result = await session.execute(query)
        logs = result.fetchall()

        daily_minutes = {}
        for row in logs:
            date_str = str(row.date)
            if date_str not in daily_minutes:
                daily_minutes[date_str] = {}
            processing_type = row.processing_type if row.processing_type else "transcription"
            daily_minutes[date_str][processing_type] = {
                "total": round(row.total_seconds / 60, 2) if row.total_seconds else 0.0,
                "speech": round(row.speech_seconds / 60, 2) if row.speech_seconds else 0.0,
                "no_speech": round(row.no_speech_seconds / 60, 2) if row.no_speech_seconds else 0.0
            }

        total_minutes = sum(
            type_data["total"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )
        speech_minutes = sum(
            type_data["speech"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )
        no_speech_minutes = sum(
            type_data["no_speech"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )

        return {
            "user_login": user.email,
            "start_date": request_data.start_date.strftime("%Y-%m-%d"),
            "end_date": request_data.end_date.strftime("%Y-%m-%d"),
            "total_minutes": round(total_minutes, 2),
            "speech_minutes": round(speech_minutes, 2),
            "no_speech_minutes": round(no_speech_minutes, 2),
            "daily_minutes": daily_minutes
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения данных: {str(e)}")


@router.get("/admin/users/")
async def admin_list_users(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_superuser),
):
    try:
        query = select(User.id, User.email).where(User.is_superuser.is_(False)).order_by(User.email)
        result = await session.execute(query)
        users = [{"id": row.id, "email": row.email} for row in result.fetchall()]
        return {"users": users}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения пользователей: {str(e)}")


@router.post("/admin/audio_usage/")
async def admin_audio_usage(
    request_data: AdminAudioUsageRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_superuser),
):
    try:
        selected_emails = request_data.user_emails
        if not selected_emails:
            emails_query = select(User.email).where(User.is_superuser.is_(False))
            emails_result = await session.execute(emails_query)
            selected_emails = [row.email for row in emails_result.fetchall()]

        query = (
            select(
                func.date(AudioLog.created_at).label("date"),
                AudioLog.processing_type.label("processing_type"),
                func.sum(AudioLog.duration_seconds).label("total_seconds"),
                func.sum(
                    case(
                        (AudioLog.has_speech.is_(False), 0),
                        else_=AudioLog.duration_seconds
                    )
                ).label("speech_seconds"),
                func.sum(
                    case(
                        (AudioLog.has_speech.is_(False), AudioLog.duration_seconds),
                        else_=0
                    )
                ).label("no_speech_seconds")
            )
            .where(AudioLog.user_login.in_(selected_emails))
            .where(AudioLog.created_at >= request_data.start_date)
            .where(AudioLog.created_at < request_data.end_date)
            .group_by(func.date(AudioLog.created_at), AudioLog.processing_type)
            .order_by(func.date(AudioLog.created_at))
        )

        result = await session.execute(query)
        logs = result.fetchall()

        daily_minutes = {}
        for row in logs:
            date_str = str(row.date)
            if date_str not in daily_minutes:
                daily_minutes[date_str] = {}
            processing_type = row.processing_type if row.processing_type else "transcription"
            daily_minutes[date_str][processing_type] = {
                "total": round(row.total_seconds / 60, 2) if row.total_seconds else 0.0,
                "speech": round(row.speech_seconds / 60, 2) if row.speech_seconds else 0.0,
                "no_speech": round(row.no_speech_seconds / 60, 2) if row.no_speech_seconds else 0.0
            }

        total_minutes = sum(
            type_data["total"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )
        speech_minutes = sum(
            type_data["speech"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )
        no_speech_minutes = sum(
            type_data["no_speech"] for date_data in daily_minutes.values() for type_data in date_data.values()
        )

        return {
            "user_login": "admin",
            "start_date": request_data.start_date.strftime("%Y-%m-%d"),
            "end_date": request_data.end_date.strftime("%Y-%m-%d"),
            "total_minutes": round(total_minutes, 2),
            "speech_minutes": round(speech_minutes, 2),
            "no_speech_minutes": round(no_speech_minutes, 2),
            "daily_minutes": daily_minutes,
            "selected_users_count": len(selected_emails),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка получения данных: {str(e)}")
