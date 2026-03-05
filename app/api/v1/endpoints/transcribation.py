import hmac
import hashlib
import json
import os
import secrets
import time
import uuid
import urllib.parse
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Optional

import aiofiles
import redis
from celery.result import AsyncResult
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydub import AudioSegment
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.db import get_async_session
from app.core.logging_config import setup_logging
from app.models import APIToken, WebhookToken
from app.models.audiolog import AudioLog
from app.tasks import (
    celery,
    submit_large_elevenlabs_task,
    transcribe_audio_task,
    transcribe_elevenlabs_task,
)
from app.utils.add_volume import auto_boost_volume
from app.utils.client_s3 import (
    build_s3_object_url,
    upload_to_s3,
)
from app.utils.large_transcription_state import (
    LARGE_TRANSCRIPTION_TTL_SECONDS,
    get_large_task,
    get_task_id_by_request_id,
    safe_json_loads,
    set_large_task,
    update_large_task,
)
from app.utils.round_duration_audio import round_duration
from app.utils.token_checker import validate_api_token
from app.utils.webhook_sender import send_webhook_with_retries
from app.utils.webhook_url_validator import validate_webhook_url

load_dotenv()
router = APIRouter()
logger = setup_logging()

TEMP_FOLDER = "temporary_files"
REDIS_URL = os.getenv("REDIS_URL")
MAX_AUDIO_DURATION = 900
MAX_FILE_SIZE = 50 * 1024 * 1024
LARGE_MAX_FILE_SIZE = 1024 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
ALLOWED_FORMAT = "wav"
RELAY_EVENT_PREFIX = "relay_event"
LARGE_FILE_DIRECT_UPLOAD_LIMIT = 20 * 1024 * 1024
os.makedirs(TEMP_FOLDER, exist_ok=True)
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


def _safe_remove_file(file_path: str) -> None:
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as exc:
        logger.warning(f"⚠️ Ошибка при удалении временного файла {file_path}: {exc}")


def _build_temp_file_path(original_filename: str) -> str:
    safe_name = os.path.basename(original_filename or "uploaded.bin")
    extension = os.path.splitext(safe_name)[1].lower()
    if not extension or len(extension) > 16:
        extension = ".bin"
    return os.path.join(TEMP_FOLDER, f"{uuid.uuid4().hex}{extension}")


def _sanitize_file_name(file_name: str) -> str:
    cleaned = os.path.basename((file_name or "").strip())
    if not cleaned:
        raise HTTPException(status_code=400, detail="file_name is required")
    return cleaned


def _normalize_cloud_storage_url(cloud_storage_url: str) -> str:
    url = (cloud_storage_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="cloud_storage_url is required")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid cloud_storage_url")
    return url


async def _save_upload_file_stream(upload_file: UploadFile, destination: str, max_bytes: int) -> int:
    total_size = 0
    try:
        async with aiofiles.open(destination, "wb") as out_file:
            while True:
                chunk = await upload_file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"The file is too large: {total_size / (1024 * 1024):.1f} MB. "
                            f"The maximum size is {max_bytes / (1024 * 1024):.0f} MB."
                        ),
                    )
                await out_file.write(chunk)
    finally:
        await upload_file.close()

    return total_size


async def _cache_signature_token_for_task(task_id: str, user_id: int, user_email: str, session: AsyncSession) -> None:
    result = await session.execute(select(WebhookToken).where(WebhookToken.user_id == user_id))
    webhook_token_entry = result.scalars().first()

    if webhook_token_entry:
        redis_client.setex(f"token:{task_id}", LARGE_TRANSCRIPTION_TTL_SECONDS, webhook_token_entry.token)
        logger.info(f"🔹 Найден webhook-токен для пользователя {user_email}")
    else:
        logger.warning(f"⚠️ Webhook-токен для пользователя {user_email} не найден!")


def _extract_elevenlabs_webhook_metadata(payload: dict) -> dict:
    metadata_candidates = [
        payload.get("webhook_metadata"),
        payload.get("metadata"),
    ]

    data_field = payload.get("data")
    if isinstance(data_field, dict):
        metadata_candidates.extend(
            [
                data_field.get("webhook_metadata"),
                data_field.get("metadata"),
            ]
        )

    for candidate in metadata_candidates:
        parsed = safe_json_loads(candidate)
        if parsed:
            return parsed

    return {}


def _extract_elevenlabs_error(payload: dict, body: dict) -> Optional[str]:
    for source in (payload, body):
        if not isinstance(source, dict):
            continue

        if source.get("error"):
            value = source.get("error")
            if isinstance(value, dict):
                return str(value.get("message") or value)
            return str(value)

        if source.get("error_message"):
            value = source.get("error_message")
            if isinstance(value, dict):
                return str(value.get("message") or value)
            return str(value)

        event_type = str(source.get("type") or "").strip().lower()
        if event_type == "webhook_error":
            return str(source.get("message") or "ElevenLabs webhook error")
    return None


def _extract_text_and_speaker_count(payload: dict) -> tuple[str, int, Optional[str]]:
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(body, dict):
        return "", 0, "Invalid callback payload"

    error_message = _extract_elevenlabs_error(payload, body)
    transcription = body.get("transcription") if isinstance(body.get("transcription"), dict) else None

    text_candidates = []
    if transcription:
        text_candidates.extend([transcription.get("text"), transcription.get("transcript")])
    text_candidates.extend(
        [
            body.get("text"),
            body.get("transcript"),
            payload.get("text"),
            payload.get("transcript"),
        ]
    )
    text = str(next((candidate for candidate in text_candidates if candidate), "")).strip()

    words = body.get("words") or payload.get("words")
    if not words and transcription:
        words = transcription.get("words")

    speakers = set()
    if isinstance(words, list):
        for item in words:
            if not isinstance(item, dict):
                continue
            speaker_id = item.get("speaker_id") or item.get("speaker")
            if speaker_id:
                speakers.add(str(speaker_id))

    speaker_count = len(speakers)
    return text, speaker_count, error_message


def _extract_provider_payload(payload: dict) -> dict:
    """
    Supports both direct provider callback and relay envelope format:
    {
      "event_id": "...",
      "payload": { ...provider payload... },
      ...
    }
    """
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        return nested_payload
    return payload


def _verify_relay_signature(headers: Mapping[str, str], raw_body: bytes) -> Optional[str]:
    signature_header = settings.downstream_hmac_header
    timestamp_header = settings.downstream_timestamp_header

    received_signature = (headers.get(signature_header) or "").strip()
    timestamp_raw = (headers.get(timestamp_header) or "").strip()

    if not received_signature or not timestamp_raw:
        return "missing_header"

    try:
        timestamp = int(timestamp_raw)
    except (TypeError, ValueError):
        return "bad_timestamp"

    tolerance = max(int(settings.relay_timestamp_tolerance_seconds), 0)
    now_unix = int(time.time())
    if abs(now_unix - timestamp) > tolerance:
        return "stale_timestamp"

    secret = settings.downstream_hmac_secret
    if not secret:
        return "signature_mismatch"
    signed_payload = f"{timestamp}.".encode() + raw_body
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_signature, received_signature):
        return "signature_mismatch"

    return None


def _extract_relay_event_id(headers: Mapping[str, str], payload: dict) -> Optional[str]:
    candidates = [
        headers.get("x-relay-event-id"),
        headers.get("x-event-id"),
        payload.get("event_id"),
        payload.get("id"),
    ]

    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("event_id"), data.get("id")])

    for candidate in candidates:
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value
    return None


def _remember_relay_event_once(event_id: str) -> bool:
    try:
        event_key = f"{RELAY_EVENT_PREFIX}:{event_id}"
        result = redis_client.set(event_key, "1", nx=True, ex=LARGE_TRANSCRIPTION_TTL_SECONDS)
        return bool(result)
    except Exception as exc:
        logger.warning(f"⚠️ Не удалось проверить дубликат event_id={event_id}: {exc}")
        return True


async def _start_large_transcription(
    *,
    token_user_id: int,
    token_user_email: str,
    session: AsyncSession,
    object_key: Optional[str],
    cloud_storage_url: Optional[str],
    original_filename: str,
    file_size_bytes: int,
    webhook_url: Optional[str],
    stream_id: Optional[str],
    is_finished_bool: bool,
) -> dict:
    task_id = str(uuid.uuid4())
    callback_token = secrets.token_urlsafe(32)
    s3_url = build_s3_object_url(object_key) if object_key else None

    large_state = {
        "status": "processing",
        "task_id": task_id,
        "user_email": token_user_email,
        "file_name": original_filename,
        "file_size_bytes": file_size_bytes,
        "s3_url": s3_url,
        "s3_object_key": object_key,
        "source_cloud_storage_url": cloud_storage_url,
        "client_webhook_url": webhook_url,
        "stream_id": stream_id,
        "is_finished": is_finished_bool,
        "callback_token": callback_token,
        "processing_type": "transcription_large",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "text": "",
        "speaker_count": 0,
        "error": None,
        "elevenlabs_request_id": None,
    }
    set_large_task(redis_client, task_id, large_state, ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS)

    if webhook_url:
        webhook_data = {
            "url": webhook_url,
            "stream_id": stream_id,
            "is_finished": is_finished_bool,
        }
        redis_client.setex(
            f"webhook:{task_id}",
            LARGE_TRANSCRIPTION_TTL_SECONDS,
            json.dumps(webhook_data),
        )
        await _cache_signature_token_for_task(task_id, token_user_id, token_user_email, session)

    log_entry = AudioLog(
        user_login=token_user_email,
        file_name=original_filename,
        duration_seconds=0,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        has_speech=None,
        task_id=task_id,
        processing_type="transcription_large",
    )
    session.add(log_entry)
    await session.commit()

    submit_large_elevenlabs_task.apply_async(
        args=[None, task_id, callback_token, object_key, cloud_storage_url],
        task_id=task_id,
    )

    return {"task_id": task_id, "status": "processing"}


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    webhook_url: str = Form(None),
    stream_id: str = Form(None),
    is_finished: str = Form("false"),
    api_token: APIToken = Depends(validate_api_token),
    session: AsyncSession = Depends(get_async_session),
):
    token_user_id = api_token.user_id
    token_user_email = api_token.user.email
    unique_file_path = os.path.join(TEMP_FOLDER, f"{uuid.uuid4().hex}.wav")
    original_filename = os.path.basename(file.filename or "uploaded.wav")

    is_finished_bool = is_finished.lower() == "true"

    file_extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if file_extension != ALLOWED_FORMAT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format: {file_extension}. Only WAV format is allowed.",
        )

    webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else webhook_url
    webhook_url = webhook_url or None

    if webhook_url:
        try:
            webhook_url = validate_webhook_url(
                webhook_url,
                allow_http=settings.allow_http_webhooks,
                allow_private_hosts=settings.allow_private_webhook_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    content = await file.read()
    file_size = len(content)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"The file is too large: {file_size / (1024 * 1024):.1f} MB. The maximum size is 50 MB.",
        )

    async with aiofiles.open(unique_file_path, "wb") as out_file:
        await out_file.write(content)

    try:
        try:
            audio = AudioSegment.from_wav(unique_file_path)
        except Exception:
            _safe_remove_file(unique_file_path)
            raise HTTPException(status_code=400, detail="The file is not a valid WAV file.")

        duration_seconds = len(audio) / 1000
        if duration_seconds > MAX_AUDIO_DURATION:
            _safe_remove_file(unique_file_path)
            raise HTTPException(
                status_code=400,
                detail=f"The file is too long: {duration_seconds:.1f} seconds. The maximum duration is 15 minutes.",
            )

        duration_seconds = round_duration(round(duration_seconds))

        log_entry = AudioLog(
            user_login=token_user_email,
            file_name=original_filename,
            duration_seconds=duration_seconds,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            has_speech=None,
            processing_type="transcription",
        )

        boosted_file_path = auto_boost_volume(unique_file_path)

        if webhook_url:
            task = transcribe_elevenlabs_task.delay(boosted_file_path, webhook_url, stream_id)
            webhook_data = {
                "url": webhook_url,
                "stream_id": stream_id,
                "is_finished": is_finished_bool,
            }
            redis_client.setex(f"webhook:{task.id}", 86400, json.dumps(webhook_data))
            log_entry.task_id = task.id
            await _cache_signature_token_for_task(task.id, token_user_id, token_user_email, session)
        else:
            task = transcribe_audio_task.delay(boosted_file_path)
            log_entry.task_id = task.id

        session.add(log_entry)
        await session.commit()

        return {"task_id": task.id, "status": "processing"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"⚠️ Ошибка в эндпоинте /transcribe: {exc}")
        _safe_remove_file(unique_file_path)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/transcribe/large")
async def transcribe_large_audio(
    file: Optional[UploadFile] = File(None),
    cloud_storage_url: str = Form(None),
    file_name: str = Form(None),
    webhook_url: str = Form(None),
    stream_id: str = Form(None),
    is_finished: str = Form("false"),
    api_token: APIToken = Depends(validate_api_token),
    session: AsyncSession = Depends(get_async_session),
    request: Request = None,
):
    token_user_id = api_token.user_id
    token_user_email = api_token.user.email
    normalized_cloud_storage_url = (
        _normalize_cloud_storage_url(cloud_storage_url) if cloud_storage_url else None
    )

    webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else webhook_url
    webhook_url = webhook_url or None

    if webhook_url:
        try:
            webhook_url = validate_webhook_url(
                webhook_url,
                allow_http=settings.allow_http_webhooks,
                allow_private_hosts=settings.allow_private_webhook_hosts,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    is_finished_bool = is_finished.lower() == "true"

    if bool(file) == bool(normalized_cloud_storage_url):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one source: multipart file or cloud_storage_url",
        )

    if normalized_cloud_storage_url:
        provided_file_name = _sanitize_file_name(file_name) if file_name else ""
        url_file_name = os.path.basename(urllib.parse.urlparse(normalized_cloud_storage_url).path or "")
        original_filename = provided_file_name or url_file_name or "cloud_media.bin"
        return await _start_large_transcription(
            token_user_id=token_user_id,
            token_user_email=token_user_email,
            session=session,
            object_key=None,
            cloud_storage_url=normalized_cloud_storage_url,
            original_filename=original_filename,
            file_size_bytes=0,
            webhook_url=webhook_url,
            stream_id=stream_id,
            is_finished_bool=is_finished_bool,
        )

    original_filename = os.path.basename(file.filename or "uploaded.bin")
    temp_file_path = _build_temp_file_path(original_filename)

    try:
        content_length_header = request.headers.get("content-length") if request else None
        if content_length_header:
            try:
                content_length = int(content_length_header)
                if content_length > LARGE_FILE_DIRECT_UPLOAD_LIMIT:
                    _safe_remove_file(temp_file_path)
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Files larger than 20 MB must be submitted via cloud_storage_url "
                            "in /transcribe/large"
                        ),
                    )
            except ValueError:
                pass

        file_size = await _save_upload_file_stream(file, temp_file_path, LARGE_MAX_FILE_SIZE)
        if file_size == 0:
            _safe_remove_file(temp_file_path)
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if file_size > LARGE_FILE_DIRECT_UPLOAD_LIMIT:
            _safe_remove_file(temp_file_path)
            raise HTTPException(
                status_code=400,
                detail=(
                    "Files larger than 20 MB must be submitted via cloud_storage_url "
                    "in /transcribe/large"
                ),
            )

        s3_object_key = f"large/{uuid.uuid4().hex}_{original_filename}"
        upload_to_s3(temp_file_path, s3_object_key)
        _safe_remove_file(temp_file_path)

        return await _start_large_transcription(
            token_user_id=token_user_id,
            token_user_email=token_user_email,
            session=session,
            object_key=s3_object_key,
            cloud_storage_url=None,
            original_filename=original_filename,
            file_size_bytes=file_size,
            webhook_url=webhook_url,
            stream_id=stream_id,
            is_finished_bool=is_finished_bool,
        )
    except HTTPException:
        _safe_remove_file(temp_file_path)
        raise
    except Exception as exc:
        logger.error(f"⚠️ Ошибка в эндпоинте /transcribe/large: {exc}")
        _safe_remove_file(temp_file_path)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/webhooks/elevenlabs")
async def receive_elevenlabs_webhook(request: Request):
    raw_body = await request.body()
    correlation_id = (request.headers.get("x-correlation-id") or "-").strip() or "-"
    invalid_reason = _verify_relay_signature(request.headers, raw_body)
    if invalid_reason:
        logger.warning(
            f"⚠️ Relay signature rejected: reason={invalid_reason} correlation_id={correlation_id}"
        )
        raise HTTPException(status_code=401, detail="invalid relay signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(payload, dict):
        logger.warning(f"⚠️ Relay payload is not an object: correlation_id={correlation_id}")
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    correlation_id = (
        (request.headers.get("x-correlation-id") or payload.get("correlation_id") or "-").strip() or "-"
    )

    if "payload" in payload and not isinstance(payload.get("payload"), dict):
        logger.warning(f"⚠️ Relay payload field is invalid: correlation_id={correlation_id}")
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    event_id = _extract_relay_event_id(request.headers, payload)
    if event_id and not _remember_relay_event_once(event_id):
        logger.info(f"ℹ️ Duplicate relay event ignored: event_id={event_id} correlation_id={correlation_id}")
        return {"status": "ignored", "reason": "duplicate_event"}

    provider_payload = _extract_provider_payload(payload)
    metadata = _extract_elevenlabs_webhook_metadata(provider_payload)
    task_id = metadata.get("task_id") if isinstance(metadata, dict) else None
    callback_token = metadata.get("callback_token") if isinstance(metadata, dict) else None

    request_id = provider_payload.get("request_id")
    if not request_id and isinstance(provider_payload.get("data"), dict):
        request_id = provider_payload["data"].get("request_id")

    resolved_via_request_id = False
    if not task_id and request_id:
        task_id = get_task_id_by_request_id(redis_client, str(request_id))
        resolved_via_request_id = bool(task_id)

    if not task_id:
        logger.warning(
            f"⚠️ Missing task identifier in webhook: event_id={event_id or '-'} "
            f"request_id={request_id or '-'} correlation_id={correlation_id}"
        )
        raise HTTPException(status_code=400, detail="Missing task identifier")

    task_state = get_large_task(redis_client, task_id)
    if not task_state:
        return {"status": "ignored", "reason": "unknown_task"}

    expected_callback_token = str(task_state.get("callback_token") or "")
    if expected_callback_token:
        if callback_token:
            if not hmac.compare_digest(expected_callback_token, str(callback_token)):
                raise HTTPException(status_code=403, detail="Invalid callback token")
        elif not resolved_via_request_id:
            raise HTTPException(status_code=403, detail="Invalid callback token")

    text, speaker_count, error_message = _extract_text_and_speaker_count(provider_payload)

    updates = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "elevenlabs_request_id": str(request_id) if request_id else task_state.get("elevenlabs_request_id"),
    }

    if error_message:
        updates.update(
            {
                "status": "failed",
                "error": error_message,
                "result_payload": payload,
            }
        )
        updated_state = update_large_task(
            redis_client,
            task_id,
            updates,
            ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS,
        )
        if updated_state and updated_state.get("client_webhook_url"):
            send_webhook_with_retries(
                updated_state["client_webhook_url"],
                payload,
                task_id,
                raw_payload=raw_body,
            )
        else:
            logger.info(
                f"ℹ️ No client webhook configured for task_id={task_id}; result available via status polling"
            )
        return {"status": "accepted", "task_id": task_id, "state": "failed"}

    updates.update(
        {
            "status": "completed",
            "text": text,
            "speaker_count": speaker_count,
            "result_payload": payload,
            "error": None,
        }
    )
    updated_state = update_large_task(
        redis_client,
        task_id,
        updates,
        ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS,
    )

    if updated_state and updated_state.get("client_webhook_url"):
        send_webhook_with_retries(
            updated_state["client_webhook_url"],
            payload,
            task_id,
            raw_payload=raw_body,
        )
    else:
        logger.info(
            f"ℹ️ No client webhook configured for task_id={task_id}; result available via status polling"
        )

    return {"status": "accepted", "task_id": task_id, "state": "completed"}


@router.get("/transcribe/status/{task_id}")
async def get_status(
    task_id: str,
    api_token: APIToken = Depends(validate_api_token),
    session: AsyncSession = Depends(get_async_session),
):
    """Проверяет статус задачи в Celery или в async flow для больших файлов."""
    try:
        owner_query = select(AudioLog.user_login).where(AudioLog.task_id == task_id)
        owner_result = await session.execute(owner_query)
        owner_login = owner_result.scalar_one_or_none()
        if owner_login is None or owner_login != api_token.user.email:
            raise HTTPException(status_code=404, detail="Task not found")

        large_state = get_large_task(redis_client, task_id)
        if large_state:
            state = str(large_state.get("status") or "processing").lower()
            if state == "completed":
                payload = large_state.get("result_payload")
                if payload is None:
                    payload = {
                        "text": large_state.get("text", ""),
                        "speaker_count": large_state.get("speaker_count", 0),
                    }
                return {
                    "task_id": task_id,
                    "status": "completed",
                    "payload": payload,
                }
            if state == "failed":
                response = {
                    "task_id": task_id,
                    "status": "failed",
                    "error": large_state.get("error", "Unknown error"),
                }
                payload = large_state.get("result_payload")
                if payload is not None:
                    response["payload"] = payload
                return response
            return {"task_id": task_id, "status": "processing"}

        task_result = AsyncResult(task_id, app=celery)
        if task_result.state == "PENDING":
            return {"task_id": task_id, "status": "processing"}

        if task_result.state == "SUCCESS":
            result = task_result.result
            if isinstance(result, dict) and "text" in result:
                return {
                    "task_id": task_id,
                    "status": "completed",
                    "text": result["text"],
                }
            return {"task_id": task_id, "status": "completed", "text": str(result)}

        if task_result.state == "FAILURE":
            return {
                "task_id": task_id,
                "status": "failed",
                "error": str(task_result.result),
            }

        return {"task_id": task_id, "status": task_result.state}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error retrieving task status: {exc}")
