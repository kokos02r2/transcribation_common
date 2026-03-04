import json
import os
from typing import Any, Optional


LARGE_TRANSCRIPTION_TTL_SECONDS = int(os.getenv("LARGE_TRANSCRIPTION_TTL_SECONDS", "604800"))
LARGE_TASK_PREFIX = "large_transcribe"
ELEVEN_REQUEST_PREFIX = "eleven_request"


def large_task_key(task_id: str) -> str:
    return f"{LARGE_TASK_PREFIX}:{task_id}"


def eleven_request_key(request_id: str) -> str:
    return f"{ELEVEN_REQUEST_PREFIX}:{request_id}"


def get_large_task(redis_client, task_id: str) -> Optional[dict]:
    raw = redis_client.get(large_task_key(task_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_large_task(redis_client, task_id: str, payload: dict, ttl_seconds: int = LARGE_TRANSCRIPTION_TTL_SECONDS) -> None:
    redis_client.setex(large_task_key(task_id), ttl_seconds, json.dumps(payload))


def update_large_task(redis_client, task_id: str, updates: dict, ttl_seconds: int = LARGE_TRANSCRIPTION_TTL_SECONDS) -> Optional[dict]:
    current = get_large_task(redis_client, task_id)
    if not current:
        return None
    current.update(updates)
    set_large_task(redis_client, task_id, current, ttl_seconds=ttl_seconds)
    return current


def set_request_mapping(redis_client, request_id: str, task_id: str, ttl_seconds: int = LARGE_TRANSCRIPTION_TTL_SECONDS) -> None:
    redis_client.setex(eleven_request_key(request_id), ttl_seconds, task_id)


def get_task_id_by_request_id(redis_client, request_id: str) -> Optional[str]:
    task_id = redis_client.get(eleven_request_key(request_id))
    if not task_id:
        return None
    return str(task_id)


def safe_json_loads(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
