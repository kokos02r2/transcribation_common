import os
import redis
from datetime import datetime, timezone
import json
import uuid

import aiofiles
from celery.result import AsyncResult
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_session
from app.models import APIToken, WebhookToken
from pydub import AudioSegment
from app.models.audiolog import AudioLog
from app.tasks import celery, transcribe_audio_task, transcribe_elevenlabs_task
from app.utils.webhook_url_validator import validate_webhook_url
from app.utils.token_checker import validate_api_token
from app.core.logging_config import setup_logging
from sqlalchemy.future import select
from app.utils.round_duration_audio import round_duration
from app.utils.add_volume import auto_boost_volume

load_dotenv()
router = APIRouter()
logger = setup_logging()

TEMP_FOLDER = "temporary_files"
REDIS_URL = os.getenv("REDIS_URL")
MAX_AUDIO_DURATION = 900
MAX_FILE_SIZE = 50 * 1024 * 1024
ALLOWED_FORMAT = "wav"
os.makedirs(TEMP_FOLDER, exist_ok=True)
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    webhook_url: str = Form(None),
    stream_id: str = Form(None),
    is_finished: str = Form('false'),
    api_token: APIToken = Depends(validate_api_token),
    session: AsyncSession = Depends(get_async_session)
):
    token_user_id = api_token.user_id
    token_user_email = api_token.user.email
    unique_file_path = os.path.join(TEMP_FOLDER, f"{uuid.uuid4().hex}.wav")
    original_filename = os.path.basename(file.filename or "uploaded.wav")

    # Преобразуем строковое значение is_finished в булево
    is_finished_bool = is_finished.lower() == 'true'

    # Проверяем расширение файла
    file_extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if file_extension != ALLOWED_FORMAT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format: {file_extension}. Only WAV format is allowed."
        )

    if webhook_url:
        try:
            webhook_url = validate_webhook_url(
                webhook_url,
                allow_http=settings.allow_http_webhooks,
                allow_private_hosts=settings.allow_private_webhook_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Читаем файл в память и проверяем размер
    content = await file.read()
    file_size = len(content)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"The file is too large: {file_size / (1024 * 1024):.1f} MB. The maximum size is 50 MB."
        )

    # Сохраняем временный файл
    async with aiofiles.open(unique_file_path, "wb") as out_file:
        await out_file.write(content)

    try:
        # Проверяем, что это действительно WAV-файл, и его длительность
        try:
            audio = AudioSegment.from_wav(unique_file_path)
        except Exception:
            os.remove(unique_file_path)
            raise HTTPException(
                status_code=400,
                detail="The file is not a valid WAV file."
            )

        duration_seconds = len(audio) / 1000  # Длительность в секундах
        if duration_seconds > MAX_AUDIO_DURATION:
            os.remove(unique_file_path)
            raise HTTPException(
                status_code=400,
                detail=f"The file is too long: {duration_seconds:.1f} seconds. The maximum duration is 15 minutes."
            )

        raw_duration = round(duration_seconds)
        duration_seconds = round_duration(raw_duration)

        log_entry = AudioLog(
            user_login=token_user_email,
            file_name=original_filename,
            duration_seconds=duration_seconds,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            has_speech=None,
            processing_type="transcription"
        )

        # 3️⃣ Авто-усиление громкости
        boosted_file_path = auto_boost_volume(unique_file_path)

        # Запускаем задачу
        if webhook_url:
            task = transcribe_elevenlabs_task.delay(boosted_file_path, webhook_url, stream_id)
            webhook_data = {
                "url": webhook_url,
                "stream_id": stream_id,
                "is_finished": is_finished_bool
            }
            redis_client.setex(f"webhook:{task.id}", 86400, json.dumps(webhook_data))
            log_entry.task_id = task.id

            # Ищем WebhookToken в БД
            result = await session.execute(select(WebhookToken).where(WebhookToken.user_id == token_user_id))
            webhook_token_entry = result.scalars().first()

            if webhook_token_entry:
                webhook_token = webhook_token_entry.token
                logger.info(f"🔹 Найден webhook-токен для пользователя {token_user_email}")
                redis_client.setex(f"token:{task.id}", 86400, webhook_token)
            else:
                logger.warning(f"⚠️ Webhook-токен для пользователя {token_user_email} не найден!")
        else:
            task = transcribe_audio_task.delay(boosted_file_path)
            log_entry.task_id = task.id

        session.add(log_entry)
        await session.commit()

        return {"task_id": task.id, "status": "processing"}
    except HTTPException as e:
        # Пробрасываем HTTPException (например, 400) напрямую
        raise e

    except Exception as e:
        logger.error(f"⚠️ Ошибка в эндпоинте /transcribe: {str(e)}")
        # Удаляем файл только в случае ошибки, если обработка не пошла дальше
        if os.path.exists(unique_file_path):
            try:
                os.remove(unique_file_path)
            except Exception as ex:
                logger.warning(f"⚠️ Ошибка при удалении временного файла {unique_file_path}: {ex}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transcribe/status/{task_id}")
async def get_status(
    task_id: str,
    api_token: APIToken = Depends(validate_api_token),
    session: AsyncSession = Depends(get_async_session)
):
    """Проверяет статус задачи в Celery"""
    try:
        owner_query = select(AudioLog.user_login).where(AudioLog.task_id == task_id)
        owner_result = await session.execute(owner_query)
        owner_login = owner_result.scalar_one_or_none()
        if owner_login is None or owner_login != api_token.user.email:
            raise HTTPException(status_code=404, detail="Task not found")

        task_result = AsyncResult(task_id, app=celery)

        if task_result.state == "PENDING":
            return {"task_id": task_id, "status": "processing"}

        elif task_result.state == "SUCCESS":
            result = task_result.result
            if isinstance(result, dict) and "text" in result:
                return {"task_id": task_id, "status": "completed", "text": result["text"]}
            return {"task_id": task_id, "status": "completed", "text": str(result)}

        elif task_result.state == "FAILURE":
            return {"task_id": task_id, "status": "failed", "error": str(task_result.result)}

        return {"task_id": task_id, "status": task_result.state}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {str(e)}")
