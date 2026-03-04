import json
import os
import mimetypes
import time
import re
import gc
import signal
from datetime import datetime, timezone
from celery import chain

import redis
import requests
import socks
import socket
from celery import Celery
from dotenv import load_dotenv
from celery import group
from pydub import AudioSegment, silence
from openai import (
    OpenAI, APIError, RateLimitError,
    APITimeoutError, APIConnectionError,
    AuthenticationError
)
from elevenlabs import ElevenLabs
import urllib.parse
from contextlib import contextmanager

from app.core.logging_config import setup_logging
from app.utils.large_transcription_state import (
    LARGE_TRANSCRIPTION_TTL_SECONDS,
    set_request_mapping,
    update_large_task,
)
from app.utils.webhook_sender import send_webhook_with_retries
from app.utils.client_s3 import upload_to_s3

load_dotenv()
logger = setup_logging()


def _safe_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@contextmanager
def _timeout(seconds: float):
    if not seconds or seconds <= 0:
        yield
        return

    def _handler(signum, frame):
        raise TimeoutError(f"Превышен таймаут запроса: {seconds} сек.")

    previous_handler = signal.signal(signal.SIGALRM, _handler)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


REDIS_URL = os.getenv("REDIS_URL")
DEFAULT_GEMINI_PROMPT = (
    'Верни дословную транскрипцию диалога врача и медпредставителя. '
    'Ответ JSON: {"text":"..."} '
    'Формат строк: |||врач||| текст или |||медицинский представитель||| текст. '
    'Без Markdown, без других полей и комментариев.'
)

# Инициализация OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Инициализация ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_PROXY_URL = os.getenv("ELEVENLABS_PROXY_URL")
ELEVENLABS_STT_API_URL = os.getenv("ELEVENLABS_STT_API_URL", "https://api.elevenlabs.io/v1/speech-to-text")
ELEVENLABS_STT_MODEL_ID = os.getenv("ELEVENLABS_STT_MODEL_ID", "scribe_v2")
ELEVENLABS_WEBHOOK_ID = os.getenv("ELEVENLABS_WEBHOOK_ID")
ELEVENLABS_LANGUAGE_CODE = os.getenv("ELEVENLABS_LANGUAGE_CODE", "ru")
# Инициализация Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3-flash-preview")
GEMINI_PROMPT = os.getenv("GEMINI_PROMPT", DEFAULT_GEMINI_PROMPT)
GEMINI_RESPONSE_JSON = os.getenv("GEMINI_RESPONSE_JSON", "true").lower() == "true"
GEMINI_TEMPERATURE = _safe_float(os.getenv("GEMINI_TEMPERATURE", "0"), 0.0)
GEMINI_TOP_P = _safe_float(os.getenv("GEMINI_TOP_P", "1"), 1.0)
GEMINI_TIMEOUT_SECONDS = _safe_float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"), 60.0)
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "0"))
# Выбор сервиса транскрибации
TRANSCRIPTION_PROVIDER = os.getenv("TRANSCRIPTION_PROVIDER", "elevenlabs")
TRANSCRIPTION_FALLBACKS = os.getenv("TRANSCRIPTION_FALLBACKS", "")
SUPPORTED_TRANSCRIPTION_PROVIDERS = {"elevenlabs", "gemini"}

# Настройки прокси (оставлены для совместимости)
PROXY_ENABLED = os.getenv("PROXY_ENABLED", "false").lower() == "true"
PROXY_HOST = os.getenv("PROXY_HOST")
PROXY_PORT = int(os.getenv("PROXY_PORT", "1080"))
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")

celery = Celery("tasks", broker=REDIS_URL, backend=REDIS_URL)

celery.conf.update(
    result_expires=86400  # Результаты хранятся 24 часа (86400 секунд)
)

WHISPER_API_URL = os.getenv("WHISPER_API_URL")
HF_TOKEN = os.getenv("HF_TOKEN")

WHISPER_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {HF_TOKEN}",
    "Content-Type": "audio/wav"
}

MAX_RETRIES = 30  # Количество попыток
RETRY_DELAY = 30  # Задержка между попытками (в секундах)

redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)


# 🔹 Разбиваем аудиофайл на части по тишине
def split_audio_on_silence(input_file, output_folder, silence_thresh=-40, min_silence_len=400, keep_silence=300):
    """
    Разрезает аудиофайл, удаляя тишину дольше указанного времени.
    Возвращает список путей к чанкам в уникальной папке.
    """
    audio = AudioSegment.from_file(input_file)

    chunks = silence.split_on_silence(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
        keep_silence=keep_silence
    )

    output_files = []
    for i, chunk in enumerate(chunks):
        output_path = os.path.join(output_folder, f"chunk_{i}.wav")
        chunk.export(output_path, format="wav")
        output_files.append(output_path)

    # Явно освобождаем память от больших объектов
    del audio, chunks
    gc.collect()
    
    return output_files


def remove_consecutive_repeated_patterns(text: str, min_word_length: int = 2, min_repeats: int = 2) -> str:
    """
    Удаляет последовательные повторяющиеся паттерны из текста, оставляя только одно вхождение.

    Args:
        text: Исходный текст
        min_word_length: Минимальная длина слова для поиска
        min_repeats: Минимальное количество последовательных повторений

    Returns:
        Текст без последовательных повторяющихся паттернов
    """
    if not text.strip():
        return text

    # Разбиваем текст на элементы, сохраняя пробелы и знаки препинания
    elements = re.split(r'(\s+|[.,!?;—])', text)
    elements = [e for e in elements if e]  # Удаляем пустые элементы

    if not elements:
        return text

    # Создаем результат, удаляя последовательные повторения
    result = []
    i = 0
    while i < len(elements):
        added = False
        # Проверяем последовательности разной длины
        for length in range(1, (len(elements) - i) // min_repeats + 1):
            pattern = elements[i:i + length]
            pattern_str = ''.join(pattern)

            # Проверяем, что в паттерне есть слова нужной длины
            if any(len(word) >= min_word_length for word in pattern if word not in ' .,!?;—'):
                repeat_count = 1
                next_idx = i + length

                # Считаем последовательные повторения
                while next_idx + length <= len(elements):
                    next_segment = elements[next_idx:next_idx + length]
                    if next_segment != pattern:
                        break
                    repeat_count += 1
                    next_idx += length

                # Если нашли нужное количество повторений
                if repeat_count >= min_repeats:
                    result.extend(pattern)
                    i = next_idx  # Пропускаем все повторения
                    added = True
                    break

        if not added:
            result.append(elements[i])
            i += 1

    # Собираем текст обратно
    result_text = ''.join(result)
    # Исправляем пробелы и форматирование
    result_text = re.sub(r'\s+', ' ', result_text)
    result_text = re.sub(r'\s+([.,!?;—])', r'\1', result_text)
    result_text = re.sub(r'([.,!?;])\s+', r'\1 ', result_text)
    result_text = re.sub(r'\s*—\s*', ' — ', result_text)

    return result_text.strip()


def _format_diarized_text(words) -> str:
    if not words:
        return ""

    lines = []
    current_speaker = None
    current_text_parts = []
    has_speaker = False

    def flush_line():
        if not current_text_parts or current_speaker is None:
            current_text_parts.clear()
            return
        text = "".join(current_text_parts).strip()
        if text:
            lines.append(f"|||{current_speaker}||| {text}")
        current_text_parts.clear()

    for item in words:
        if isinstance(item, dict):
            speaker_id = item.get("speaker_id")
            token_text = item.get("text")
        else:
            speaker_id = getattr(item, "speaker_id", None)
            token_text = getattr(item, "text", None)

        if speaker_id is not None:
            has_speaker = True
            if current_speaker is None:
                current_speaker = speaker_id
            elif speaker_id != current_speaker:
                flush_line()
                current_speaker = speaker_id

        if token_text is None:
            continue
        if current_speaker is None:
            continue
        current_text_parts.append(str(token_text))

    flush_line()
    if not has_speaker:
        return ""
    return "\n".join(lines)


def _normalize_provider(value: str) -> str:
    if not value:
        return ""
    return value.strip().lower()


def _build_provider_chain(primary: str, fallbacks: str) -> list:
    providers = []

    def add_provider(item: str) -> None:
        if item in SUPPORTED_TRANSCRIPTION_PROVIDERS and item not in providers:
            providers.append(item)

    add_provider(_normalize_provider(primary))

    if fallbacks:
        for item in fallbacks.split(","):
            add_provider(_normalize_provider(item))
    else:
        if providers:
            if providers[0] == "elevenlabs":
                add_provider("gemini")
            elif providers[0] == "gemini":
                add_provider("elevenlabs")

    if not providers:
        providers = ["elevenlabs", "gemini"]

    return providers


def _get_transcription_providers() -> list:
    providers = _build_provider_chain(TRANSCRIPTION_PROVIDER, TRANSCRIPTION_FALLBACKS)
    if not providers:
        providers = ["elevenlabs", "gemini"]
    return providers


def _guess_mime_type(path: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or "application/octet-stream"


def _prepare_socks5_proxy() -> tuple:
    if not ELEVENLABS_PROXY_URL or not ELEVENLABS_PROXY_URL.startswith("socks5://"):
        return None, "Прокси не настроен"

    logger.info("🌐 Используем прокси из ELEVENLABS_PROXY_URL")

    parsed_url = urllib.parse.urlparse(ELEVENLABS_PROXY_URL)
    proxy_host = parsed_url.hostname
    proxy_port = parsed_url.port or 1080
    proxy_username = parsed_url.username
    proxy_password = parsed_url.password

    proxy_check_attempts = 0
    max_proxy_check_attempts = 3

    while proxy_check_attempts < max_proxy_check_attempts:
        if check_proxy_connection(proxy_host, proxy_port, proxy_username, proxy_password):
            logger.info("✅ Подключение к прокси успешно установлено")
            return (proxy_host, proxy_port, proxy_username, proxy_password), None
        proxy_check_attempts += 1
        if proxy_check_attempts < max_proxy_check_attempts:
            logger.warning(
                f"⚠️ Попытка {proxy_check_attempts} подключения к прокси не удалась, повторяем через 5 секунд..."
            )
            time.sleep(5)
        else:
            logger.error("❌ Все попытки подключения к прокси не удались")
            return None, "Не удалось установить подключение к прокси"

    return None, "Не удалось установить подключение к прокси"


def _build_requests_proxy_kwargs() -> dict:
    if not ELEVENLABS_PROXY_URL:
        return {}
    return {"proxies": {"http": ELEVENLABS_PROXY_URL, "https": ELEVENLABS_PROXY_URL}}


def _mark_large_task_failed(task_id: str, error_message: str) -> None:
    update_large_task(
        redis_client,
        task_id,
        {
            "status": "failed",
            "error": error_message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS,
    )


def _transcribe_with_gemini(audio_data: bytes, file_path: str) -> dict:
    if not GEMINI_API_KEY:
        return {"status": "failed", "error": "GEMINI_API_KEY не задан"}

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        return {"status": "failed", "error": f"Gemini SDK не доступен: {exc}"}

    proxy_settings, proxy_error = _prepare_socks5_proxy()
    if proxy_error:
        logger.error("❌ Не удалось настроить прокси для Gemini")
        return {"status": "failed", "error": proxy_error}

    proxy_host, proxy_port, proxy_username, proxy_password = proxy_settings

    try:
        prompt = GEMINI_PROMPT
        config_kwargs = {
            "temperature": GEMINI_TEMPERATURE,
            "top_p": GEMINI_TOP_P,
        }
        try:
            thinking_config = types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MINIMAL,
                include_thoughts=False,
            )
        except Exception:
            thinking_config = {
                "thinking_level": "minimal",
                "include_thoughts": False,
            }

        config_kwargs["thinking_config"] = thinking_config
        if GEMINI_MAX_OUTPUT_TOKENS > 0:
            config_kwargs["max_output_tokens"] = GEMINI_MAX_OUTPUT_TOKENS
        if GEMINI_RESPONSE_JSON:
            prompt = (
                f"{prompt}"
            )
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        with proxy_context(
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password
        ):
            client = genai.Client(api_key=GEMINI_API_KEY)

            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(
                            data=audio_data,
                            mime_type=_guess_mime_type(file_path),
                        ),
                    ],
                ),
            ]

            if GEMINI_TIMEOUT_SECONDS > 0:
                with _timeout(GEMINI_TIMEOUT_SECONDS):
                    response = client.models.generate_content(
                        model=GEMINI_MODEL_NAME,
                        contents=contents,
                        config=config,
                    )
            else:
                response = client.models.generate_content(
                    model=GEMINI_MODEL_NAME,
                    contents=contents,
                    config=config,
                )

        response_text = response.text or ""
        usage_metadata = getattr(response, "usage_metadata", None)
        output_tokens = None
        if usage_metadata is not None:
            audio_tokens = None
            text_tokens = None
            thoughts_tokens = getattr(usage_metadata, "thoughts_token_count", None)
            prompt_details = getattr(usage_metadata, "prompt_tokens_details", None)
            output_tokens = getattr(usage_metadata, "candidates_token_count", None)
            if prompt_details:
                for item in prompt_details:
                    modality = getattr(item, "modality", None)
                    if hasattr(modality, "value"):
                        modality_value = str(modality.value).lower()
                    else:
                        modality_value = str(modality).lower()
                    token_count = getattr(item, "token_count", None)
                    if "audio" in modality_value:
                        audio_tokens = token_count
                    elif "text" in modality_value:
                        text_tokens = token_count

            logger.info(
                "📊 Gemini tokens audio=%s text_in=%s output=%s thoughts=%s total=%s response_text_len=%s",
                audio_tokens,
                text_tokens,
                output_tokens,
                thoughts_tokens,
                getattr(usage_metadata, "total_token_count", None),
                len(response_text),
            )
            logger.info("📊 Gemini usage_metadata=%s", usage_metadata)
        else:
            logger.info("📊 Gemini response_text_len=%s", len(response_text))
        if (
            GEMINI_MAX_OUTPUT_TOKENS > 0
            and output_tokens is not None
            and output_tokens >= GEMINI_MAX_OUTPUT_TOKENS
        ):
            logger.warning(
                "⚠️ Gemini достиг лимита output токенов (%s/%s), используем fallback",
                output_tokens,
                GEMINI_MAX_OUTPUT_TOKENS,
            )
            return {
                "status": "failed",
                "error": (
                    "Gemini достиг лимита output токенов "
                    f"({output_tokens}/{GEMINI_MAX_OUTPUT_TOKENS})"
                ),
            }
        if GEMINI_RESPONSE_JSON:
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError as exc:
                return {"status": "failed", "error": f"Некорректный JSON от Gemini: {exc}"}

            if isinstance(payload, dict):
                text = str(payload.get("text", "")).strip()
            elif isinstance(payload, list):
                first_item = payload[0] if payload else None
                if isinstance(first_item, dict):
                    text = str(first_item.get("text", "")).strip()
                else:
                    text = ""
            else:
                text = ""
        else:
            text = response_text.strip()

        if not text:
            if GEMINI_RESPONSE_JSON:
                payload_type = type(payload).__name__
                payload_keys = list(payload.keys()) if isinstance(payload, dict) else None
                response_snippet = response_text[:500]
                logger.warning(
                    "⚠️ Пустой текст после JSON парсинга Gemini: payload_type=%s payload_keys=%s response_snippet=%s",
                    payload_type,
                    payload_keys,
                    response_snippet,
                )
            else:
                logger.warning(
                    "⚠️ Пустой текст от Gemini: response_snippet=%s",
                    response_text[:500],
                )
            return {"status": "failed", "error": "Пустой ответ от Gemini"}

        cleaned_text = remove_consecutive_repeated_patterns(text)
        return {"status": "completed", "text": cleaned_text, "speaker_count": 0}
    except Exception as exc:
        return {"status": "failed", "error": f"Ошибка Gemini: {exc}"}


@celery.task(bind=True)
def transcribe_audio_task(self, file_path: str):
    """Фоновая задача обработки аудиофайла с повторными попытками"""
    try:
        with open(file_path, "rb") as f:
            audio_data = f.read()
        attempt = 0
        while attempt < MAX_RETRIES:
            try:
                response = requests.post(WHISPER_API_URL, headers=WHISPER_HEADERS, data=audio_data, timeout=120)
                logger.info(f"Попытка {attempt + 1}: {response.status_code}")

                if response.status_code == 200:
                    result = {"status": "completed", "text": response.json().get("text", "").strip()}
                    # Освобождаем память от audio_data сразу после успешного запроса
                    del audio_data
                    gc.collect()
                    break
                elif response.status_code == 503:
                    logger.info(f"Whisper API вернул 503. Попытка {attempt + 1} из {MAX_RETRIES}. Ждем {RETRY_DELAY} сек...")
                    time.sleep(RETRY_DELAY)  # Ждём перед повторной попыткой
                    attempt += 1
                else:
                    result = {"status": "failed", "error": response.text}
                    break  # Если ошибка не 503, сразу выходим

            except requests.exceptions.RequestException as e:
                logger.info(f"Ошибка сети (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1
        else:
            result = {"status": "failed", "error": "Максимальное число попыток исчерпано"}
            # Освобождаем память при исчерпании попыток
            try:
                del audio_data
                gc.collect()
            except:
                pass

    except Exception as e:
        result = {"status": "failed", "error": str(e)}
        # Освобождаем память при ошибке
        try:
            del audio_data
            gc.collect()
        except:
            pass

    # Удаляем файл после завершения задачи
    # try:
    #     os.remove(file_path)
    # except Exception as e:
    #     logger.info(f"Ошибка при удалении файла {file_path}: {str(e)}")

    return result


@celery.task(bind=True)
def merge_transcriptions_task(self, transcriptions, file_path: str, webhook_url: str, stream_id: str, parent_task_id: str):
    """Объединяет результаты транскрибации и отправляет на вебхук."""
    
    # Уникальная папка для чанков
    output_folder = os.path.join(os.path.dirname(file_path), f"chunks_{parent_task_id}")

    try:
        logger.info(f"📝 Получены данные: {transcriptions}")

        # Если transcriptions — это один словарь, превращаем в список
        if isinstance(transcriptions, dict):
            transcriptions = [transcriptions]

        # Проверяем, что передан список
        if not isinstance(transcriptions, list):
            logger.error("❌ Ошибка: transcriptions должен быть списком или словарем!")
            return {"status": "failed", "error": "Invalid transcriptions format"}

        # Извлекаем текст из транскрипций
        extracted_texts = [t.get("text", "").strip() for t in transcriptions if isinstance(t, dict) and "text" in t]

        # Объединяем текст
        combined_text = " ".join(filter(None, extracted_texts)).strip()
        
        # Удаляем повторяющиеся паттерны
        cleaned_text = remove_consecutive_repeated_patterns(combined_text)
        
        logger.info(f"✅ Объединенный и очищенный текст: {cleaned_text[:200]}...")

        # Оцениваем количество спикеров на основе транскрипций
        speaker_count = 0
        for t in transcriptions:
            if isinstance(t, dict) and "speaker_count" in t:
                speaker_count = max(speaker_count, t.get("speaker_count", 0))

        # Получаем информацию о вебхуке, включая флаг is_finished
        webhook_info = redis_client.get(f"webhook:{parent_task_id}")
        is_finished = False
        if webhook_info:
            try:
                webhook_data = json.loads(webhook_info)
                is_finished = webhook_data.get("is_finished", False)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при получении данных вебхука: {str(e)}")

        result_data = {
            "stream_id": stream_id,
            "text": cleaned_text,
            "type": "transcription",
            "speaker_count": speaker_count,
            "is_finished": is_finished
        }

        # Отправляем результат через вебхук
        send_webhook_with_retries(webhook_url, result_data, parent_task_id)

        # Удаляем исходный файл и папку с чанками
        try:
            os.remove(file_path)
            logger.info(f"🗑 Файл {file_path} успешно удалён.")
            import shutil
            shutil.rmtree(output_folder)
            logger.info(f"🗑 Папка {output_folder} успешно удалена.")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка удаления файла/папки: {str(e)}")

        return {"status": "completed", "text": cleaned_text, "speaker_count": speaker_count}

    except Exception as e:
        logger.error(f"⚠️ Ошибка в merge_transcriptions_task: {str(e)}")
        # Очищаем временную папку в случае ошибки
        try:
            import shutil
            shutil.rmtree(output_folder)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка очистки папки {output_folder}: {str(e)}")
        return {"status": "failed", "error": str(e)}


@celery.task(bind=True)
def transcribe_and_send_webhook_task(self, file_path: str, webhook_url: str, stream_id: str):
    try:
        if not os.path.exists(file_path):
            logger.error(f"❌ Файл {file_path} не найден перед обработкой!")
            return {"status": "failed", "error": f"File {file_path} not found"}

        logger.info(f"🔹 Разбиваем файл {file_path} на сегменты. Task ID: {self.request.id}")

        output_folder = os.path.join(os.path.dirname(file_path), f"chunks_{self.request.id}")
        os.makedirs(output_folder, exist_ok=True)

        chunks = split_audio_on_silence(file_path, output_folder)
        if not chunks:
            logger.warning("⚠️ В файле не обнаружено речи.")
            try:
                import shutil
                shutil.rmtree(output_folder)
                os.remove(file_path)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка очистки: {str(e)}")
            return {"status": "empty_audio"}

        logger.info(f"✅ Разбито на {len(chunks)} сегментов.")

        transcribe_tasks = group(transcribe_audio_task.s(chunk) for chunk in chunks)
        chain_result = chain(
            transcribe_tasks,
            merge_transcriptions_task.s(file_path, webhook_url, stream_id, self.request.id)
        ).apply_async()

        logger.info("🚀 Запущена цепочка задач.")
        return {"status": "processing", "chain_id": chain_result.id}

    except Exception as e:
        logger.error(f"⚠️ Ошибка в transcribe_and_send_webhook_task: {str(e)}", exc_info=True)
        try:
            import shutil
            shutil.rmtree(output_folder)
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка очистки: {str(e)}")
        return {"status": "failed", "error": str(e)}


@celery.task
def send_webhook_task(transcription_result, file_path: str, webhook_url: str, stream_id: str, parent_task_id: str):
    try:
        if transcription_result.get("status") == "completed":
            final_text = transcription_result.get("text", "").strip()
            # Получаем количество спикеров, если оно есть в результате
            speaker_count = transcription_result.get("speaker_count", 0)
        else:
            logger.warning(f"⚠️ Ошибка транскрибации файла {file_path}: {transcription_result.get('error')}")
            return {"status": "failed", "error": transcription_result.get("error")}

        logger.info(f"✅ Финальный текст сформирован: {final_text[:100]}...")

        # Получаем информацию о вебхуке, включая флаг is_finished
        webhook_info = redis_client.get(f"webhook:{parent_task_id}")
        is_finished = False
        if webhook_info:
            try:
                webhook_data = json.loads(webhook_info)
                is_finished = webhook_data.get("is_finished", False)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при получении данных вебхука: {str(e)}")

        # Отправляем результат через вебхук
        result_data = {
            "stream_id": stream_id,
            "text": final_text,
            "type": "transcription",
            "speaker_count": speaker_count,
            "is_finished": is_finished
        }
        send_webhook_with_retries(webhook_url, result_data, parent_task_id)

        # Удаляем исходный аудиофайл
        try:
            os.remove(file_path)
            logger.info(f"🗑 Файл {file_path} успешно удалён.")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка удаления файла {file_path}: {str(e)}")

        return {"status": "completed", "text": final_text, "speaker_count": speaker_count}

    except Exception as e:
        logger.error(f"⚠️ Ошибка в таске send_webhook_task: {str(e)}", exc_info=True)
        return {"status": "failed", "error": str(e)}


@celery.task(bind=True)
def transcribe_full_audio_task(self, file_path: str, webhook_url: str, stream_id: str):
    """
    Отправляет весь аудиофайл целиком в OpenAI Whisper API для транскрибации.

    Args:
        file_path: Путь к аудиофайлу
        webhook_url: URL для отправки результата
        stream_id: Идентификатор потока
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"❌ Файл {file_path} не найден перед обработкой!")
            return {"status": "failed", "error": f"File {file_path} not found"}

        logger.info(f"🔹 Отправляем файл {file_path} целиком в OpenAI Whisper API. Task ID: {self.request.id}")

        # Инициализируем клиент OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        # Отправляем файл в OpenAI Whisper API
        attempt = 0
        while attempt < MAX_RETRIES:
            try:
                logger.info(f"Попытка {attempt + 1}: отправка файла в OpenAI Whisper API")

                with open(file_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        file=audio_file,
                        model="gpt-4o-transcribe",
                        language="ru",
                        temperature=0.1
                    )

                logger.info(f"Попытка {attempt + 1}: успешно получен ответ от API")

                if result and hasattr(result, 'text'):
                    text = result.text.strip()

                    # Очищаем текст от повторений
                    words = None
                    if isinstance(result, dict) and "words" in result:
                        words = result.get("words")
                    elif hasattr(result, "words"):
                        words = result.words

                    formatted_text = _format_diarized_text(words)
                    if formatted_text:
                        text = formatted_text

                    cleaned_text = remove_consecutive_repeated_patterns(text)

                    logger.info(f"✅ Получен текст: {cleaned_text[:200]}...")

                    # Подсчитываем количество уникальных спикеров
                    speaker_count = 0
                    try:
                        # Проверяем есть ли words в ответе API
                        if isinstance(result, dict) and 'words' in result:
                            speakers = set()
                            for word in result['words']:
                                if 'speaker_id' in word and word['speaker_id']:
                                    speakers.add(word['speaker_id'])
                            speaker_count = len(speakers)
                            logger.info(f"✅ Обнаружено {speaker_count} уникальных спикеров: {', '.join(speakers)}")
                        elif hasattr(result, 'words'):
                            speakers = set()
                            for word in result.words:
                                if hasattr(word, 'speaker_id') and word.speaker_id:
                                    speakers.add(word.speaker_id)
                            speaker_count = len(speakers)
                            logger.info(f"✅ Обнаружено {speaker_count} уникальных спикеров: {', '.join(speakers)}")
                        else:
                            logger.warning("⚠️ Не удалось найти информацию о спикерах в ответе API")
                    except Exception as speaker_error:
                        logger.error(f"❌ Ошибка при подсчете спикеров: {str(speaker_error)}")
                        speaker_count = 0

                    # Отправляем результат через вебхук
                    result_data = {
                        "stream_id": stream_id,
                        "text": cleaned_text,
                        "type": "transcription",
                        "speaker_count": speaker_count
                    }
                    send_webhook_with_retries(webhook_url, result_data, self.request.id)

                    # Удаляем исходный файл
                    try:
                        os.remove(file_path)
                        logger.info(f"🗑 Файл {file_path} успешно удалён.")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка удаления файла: {str(e)}")

                    return {"status": "completed", "text": cleaned_text, "speaker_count": speaker_count}
                else:
                    error_msg = "Неверный формат ответа от OpenAI API"
                    logger.error(error_msg)
                    return {"status": "failed", "error": error_msg}

            except RateLimitError as e:
                logger.error(f"Превышен лимит запросов (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1
            except APITimeoutError as e:
                logger.error(f"Таймаут API (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1
            except APIConnectionError as e:
                logger.error(f"Ошибка подключения к API (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1
            except AuthenticationError as e:
                logger.error(f"Ошибка аутентификации: {str(e)}")
                return {"status": "failed", "error": "Ошибка аутентификации API"}
            except APIError as e:
                logger.error(f"Ошибка API OpenAI (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1
            except Exception as e:
                logger.error(f"Неожиданная ошибка (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY)
                attempt += 1

        return {"status": "failed", "error": "Максимальное число попыток исчерпано"}

    except Exception as e:
        logger.error(f"⚠️ Ошибка в transcribe_full_audio_task: {str(e)}", exc_info=True)
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка удаления файла: {str(e)}")
        return {"status": "failed", "error": str(e)}


def check_proxy_connection(proxy_host: str, proxy_port: int, proxy_username: str = None, proxy_password: str = None) -> bool:
    """
    Проверяет подключение к SOCKS5 прокси.

    Args:
        proxy_host: Хост прокси
        proxy_port: Порт прокси
        proxy_username: Имя пользователя прокси (опционально)
        proxy_password: Пароль прокси (опционально)

    Returns:
        bool: True если подключение успешно, False в противном случае
    """
    try:
        # Создаем тестовый сокет
        test_socket = socks.socksocket()
        test_socket.set_proxy(
            proxy_type=socks.SOCKS5,
            addr=proxy_host,
            port=proxy_port,
            username=proxy_username,
            password=proxy_password
        )
        # Устанавливаем таймаут в 5 секунд
        test_socket.settimeout(5)
        # Пробуем подключиться к какому-нибудь публичному сервису через прокси
        test_socket.connect(("api.ipify.org", 80))
        test_socket.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка проверки подключения к прокси: {str(e)}")
        return False


@contextmanager
def proxy_context(proxy_host: str, proxy_port: int, proxy_username: str = None, proxy_password: str = None):
    """
    Контекстный менеджер для безопасной работы с прокси в многопоточной среде.

    Args:
        proxy_host: Хост прокси
        proxy_port: Порт прокси
        proxy_username: Имя пользователя прокси (опционально)
        proxy_password: Пароль прокси (опционально)
    """
    original_socket = socket.socket
    try:
        # Настраиваем прокси только для текущего потока
        socks.set_default_proxy(
            socks.SOCKS5,
            proxy_host,
            proxy_port,
            username=proxy_username,
            password=proxy_password
        )
        socket.socket = socks.socksocket
        yield
    finally:
        # Восстанавливаем сокет только для текущего потока
        socket.socket = original_socket


def _transcribe_with_elevenlabs(audio_data: bytes, file_path: str) -> dict:
    if not ELEVENLABS_API_KEY:
        return {"status": "failed", "error": "ELEVENLABS_API_KEY не задан"}

    proxy_settings, proxy_error = _prepare_socks5_proxy()
    if proxy_error:
        logger.error("❌ Не удалось настроить прокси")
        return {"status": "failed", "error": proxy_error}

    proxy_host, proxy_port, proxy_username, proxy_password = proxy_settings

    # Отправляем файл в ElevenLabs API
    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            logger.info(f"Попытка {attempt + 1}: отправка файла в ElevenLabs API")

            # Используем контекстный менеджер для безопасной работы с прокси
            with proxy_context(
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                proxy_username=proxy_username,
                proxy_password=proxy_password
            ):
                # Инициализируем клиент ElevenLabs
                client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

                # Отправляем запрос через прокси
                result = client.speech_to_text.convert(
                    model_id="scribe_v2",
                    file=audio_data,
                    language_code="ru",
                    tag_audio_events=False,
                    diarize=True
                )

            logger.info(f"Попытка {attempt + 1}: успешно получен ответ от API")

            # Проверяем формат ответа
            if result:
                logger.info(result)
                try:
                    # Если результат - JSON или словарь
                    if isinstance(result, dict) and 'text' in result:
                        text = result['text'].strip()
                        logger.info("✅ Успешно получен текст из JSON ответа")
                    # Если результат - объект с атрибутом text
                    elif hasattr(result, 'text'):
                        text = result.text.strip()
                        logger.info("✅ Успешно получен текст из объекта с атрибутом text")
                    # Для других форматов пробуем преобразовать в словарь
                    else:
                        result_dict = result
                        if not isinstance(result, dict):
                            # Пытаемся преобразовать в словарь
                            logger.info("⚠️ Пытаемся преобразовать результат в словарь")
                            # Используем глобальный json, а не локальный импорт
                            try:
                                if isinstance(result, str):
                                    result_dict = json.loads(result)
                                else:
                                    # Если не строка и не словарь, пробуем через __dict__
                                    result_dict = result.__dict__
                            except Exception as e:
                                logger.warning(f"⚠️ Не удалось преобразовать в словарь: {str(e)}")
                                result_dict = {}

                        # Проверяем словарь
                        if isinstance(result_dict, dict) and 'text' in result_dict:
                            text = result_dict['text'].strip()
                            logger.info("✅ Успешно получен текст после преобразования")
                        else:
                            # Пробуем найти текст в строковом представлении
                            logger.warning("⚠️ Пытаемся извлечь текст из строкового представления")
                            result_str = str(result)
                            # Ищем поле text='...' или text": "...' в строке
                            text_match = re.search(r"text['\"]?\s*[=:]\s*['\"]([^'\"]+)['\"]", result_str)
                            if text_match:
                                text = text_match.group(1).strip()
                                logger.info("✅ Успешно извлечен текст из строкового представления")
                            else:
                                logger.error("❌ Не удалось извлечь текст из ответа")
                                return {"status": "failed", "error": "Не удалось извлечь текст из ответа API"}

                    words = None
                    if isinstance(result, dict) and "words" in result:
                        words = result.get("words")
                    elif hasattr(result, "words"):
                        words = result.words
                    elif isinstance(result, str):
                        try:
                            parsed_result = json.loads(result)
                            if isinstance(parsed_result, dict):
                                words = parsed_result.get("words")
                        except Exception:
                            words = None

                    formatted_text = _format_diarized_text(words)
                    if formatted_text:
                        text = formatted_text

                    cleaned_text = remove_consecutive_repeated_patterns(text)

                    logger.info(f"✅ Получен текст: {cleaned_text[:200]}...")

                    # Подсчитываем количество уникальных спикеров
                    speaker_count = 0
                    try:
                        # Проверяем есть ли words в ответе API
                        if isinstance(result, dict) and 'words' in result:
                            speakers = set()
                            for word in result['words']:
                                if 'speaker_id' in word and word['speaker_id']:
                                    speakers.add(word['speaker_id'])
                            speaker_count = len(speakers)
                            logger.info(f"✅ Обнаружено {speaker_count} уникальных спикеров: {', '.join(speakers)}")
                        elif hasattr(result, 'words'):
                            speakers = set()
                            for word in result.words:
                                if hasattr(word, 'speaker_id') and word.speaker_id:
                                    speakers.add(word.speaker_id)
                            speaker_count = len(speakers)
                            logger.info(f"✅ Обнаружено {speaker_count} уникальных спикеров: {', '.join(speakers)}")
                        else:
                            logger.warning("⚠️ Не удалось найти информацию о спикерах в ответе API")
                    except Exception as speaker_error:
                        logger.error(f"❌ Ошибка при подсчете спикеров: {str(speaker_error)}")
                        speaker_count = 0

                    return {"status": "completed", "text": cleaned_text, "speaker_count": speaker_count}
                except Exception as parse_error:
                    logger.error(f"❌ Ошибка при обработке ответа: {str(parse_error)}")
                    return {"status": "failed", "error": f"Ошибка обработки ответа: {str(parse_error)}"}

            logger.warning("⚠️ Получен пустой ответ от API")
            error_msg = "Неверный формат ответа от ElevenLabs API"
            logger.error(error_msg)
            return {"status": "failed", "error": error_msg}

        except Exception as e:
            logger.error(f"Ошибка API ElevenLabs (попытка {attempt + 1} из {MAX_RETRIES}): {str(e)}")
            time.sleep(RETRY_DELAY)
            attempt += 1

    return {"status": "failed", "error": "Максимальное число попыток исчерпано"}


@celery.task(bind=True)
def submit_large_elevenlabs_task(self, file_path: str, task_id: str, callback_token: str):
    """
    Отправляет большой файл в ElevenLabs в webhook-режиме.
    Финальный результат приходит на внутренний endpoint /webhooks/elevenlabs.
    """
    try:
        if not os.path.exists(file_path):
            _mark_large_task_failed(task_id, f"File {file_path} not found")
            return {"status": "failed", "error": f"File {file_path} not found"}

        if not ELEVENLABS_API_KEY:
            _mark_large_task_failed(task_id, "ELEVENLABS_API_KEY не задан")
            return {"status": "failed", "error": "ELEVENLABS_API_KEY not configured"}

        webhook_metadata = json.dumps(
            {"task_id": task_id, "callback_token": callback_token},
            separators=(",", ":"),
            ensure_ascii=False,
        )

        data = {
            "model_id": ELEVENLABS_STT_MODEL_ID,
            "diarize": "true",
            "tag_audio_events": "false",
            "webhook": "true",
            "webhook_metadata": webhook_metadata,
        }
        if ELEVENLABS_LANGUAGE_CODE:
            data["language_code"] = ELEVENLABS_LANGUAGE_CODE
        if ELEVENLABS_WEBHOOK_ID:
            data["webhook_id"] = ELEVENLABS_WEBHOOK_ID

        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        proxy_kwargs = _build_requests_proxy_kwargs()

        with open(file_path, "rb") as audio_file:
            files = {
                "file": (
                    os.path.basename(file_path),
                    audio_file,
                    _guess_mime_type(file_path),
                )
            }
            response = requests.post(
                ELEVENLABS_STT_API_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=600,
                **proxy_kwargs,
            )

        response_text = response.text[:1000]
        if not response.ok:
            error_message = (
                f"ElevenLabs submit failed: {response.status_code} {response_text}"
            )
            _mark_large_task_failed(task_id, error_message)
            return {"status": "failed", "error": error_message}

        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {}

        request_id = (
            response_payload.get("request_id")
            or response_payload.get("id")
            or response_payload.get("transcription_id")
        )

        updates = {
            "status": "processing",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
        if request_id:
            updates["elevenlabs_request_id"] = str(request_id)
            set_request_mapping(
                redis_client,
                str(request_id),
                task_id,
                ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS,
            )

        update_large_task(
            redis_client,
            task_id,
            updates,
            ttl_seconds=LARGE_TRANSCRIPTION_TTL_SECONDS,
        )
        return {"status": "submitted", "request_id": request_id}

    except requests.RequestException as exc:
        error_message = f"Ошибка сети при отправке в ElevenLabs: {exc}"
        _mark_large_task_failed(task_id, error_message)
        return {"status": "failed", "error": error_message}
    except Exception as exc:
        error_message = f"Ошибка submit_large_elevenlabs_task: {exc}"
        _mark_large_task_failed(task_id, error_message)
        return {"status": "failed", "error": error_message}
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"🗑 Файл {file_path} удалён после отправки в ElevenLabs.")
        except Exception as exc:
            logger.warning(f"⚠️ Ошибка удаления файла {file_path}: {exc}")


@celery.task(bind=True)
def transcribe_elevenlabs_task(self, file_path: str, webhook_url: str, stream_id: str):
    """
    Транскрибирует аудио через выбранный сервис и отправляет результат в вебхук.

    Args:
        file_path: Путь к аудиофайлу
        webhook_url: URL для отправки результата
        stream_id: Идентификатор потока
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"❌ Файл {file_path} не найден перед обработкой!")
            return {"status": "failed", "error": f"File {file_path} not found"}

        logger.info(f"🔹 Подготавливаем файл {file_path} для транскрибации. Task ID: {self.request.id}")

        # Подготавливаем аудиофайл
        with open(file_path, "rb") as audio_file:
            audio_data = audio_file.read()

        providers = _get_transcription_providers()
        logger.info(f"🔹 Провайдеры транскрибации: {', '.join(providers)}")

        last_error = None
        for provider in providers:
            if provider == "elevenlabs":
                result = _transcribe_with_elevenlabs(audio_data, file_path)
            elif provider == "gemini":
                result = _transcribe_with_gemini(audio_data, file_path)
            else:
                logger.warning(f"⚠️ Неизвестный провайдер транскрибации: {provider}")
                continue

            if result and result.get("status") == "completed":
                cleaned_text = result.get("text", "")
                speaker_count = result.get("speaker_count", 0)

                # Получаем информацию о вебхуке, включая флаг is_finished
                webhook_info = redis_client.get(f"webhook:{self.request.id}")
                is_finished = False
                if webhook_info:
                    try:
                        webhook_data = json.loads(webhook_info)
                        is_finished = webhook_data.get("is_finished", False)
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка при получении данных вебхука: {str(e)}")

                # Отправляем результат через вебхук
                result_data = {
                    "stream_id": stream_id,
                    "text": cleaned_text,
                    "type": "transcription",
                    "speaker_count": speaker_count,
                    "is_finished": is_finished
                }
                send_webhook_with_retries(webhook_url, result_data, self.request.id)

                try:
                    s3_file_name = os.path.basename(file_path)
                    s3_url = upload_to_s3(file_path, s3_file_name)
                    logger.info(f"✅ Файл загружен в S3: {s3_url}")
                    os.remove(file_path)
                    logger.info(f"🗑 Файл {file_path} успешно удалён после загрузки в S3.")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка загрузки файла в S3: {str(e)}")

                # Освобождаем память от audio_data
                del audio_data
                gc.collect()

                return {"status": "completed", "text": cleaned_text, "speaker_count": speaker_count}

            last_error = result.get("error") if result else "Неизвестная ошибка"
            logger.warning(f"⚠️ Провайдер {provider} завершился ошибкой: {last_error}")

        # Освобождаем память, если все провайдеры не сработали
        try:
            del audio_data
            gc.collect()
        except Exception:
            pass

        return {"status": "failed", "error": last_error or "Все провайдеры транскрибации завершились ошибкой"}

    except Exception as e:
        logger.error(f"⚠️ Ошибка в transcribe_elevenlabs_task: {str(e)}", exc_info=True)
        # Освобождаем память при ошибке
        try:
            del audio_data
            gc.collect()
        except:
            pass
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"⚠️ Ошибка удаления файла: {str(e)}")
        return {"status": "failed", "error": str(e)}
