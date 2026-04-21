import re
from collections.abc import Mapping
from typing import Any

_PROVIDER_LABEL = "transcription provider"
_REDACT_TEXT_FIELDS = {"text", "transcript", "word"}
_ELEVENLABS_URL_PATTERN = re.compile(r"https?://api\.elevenlabs\.io[^\s'\"),;]*", re.IGNORECASE)
_ELEVENLABS_HOST_PATTERN = re.compile(r"\bapi\.elevenlabs\.io\b", re.IGNORECASE)
_ELEVENLABS_ENV_PATTERN = re.compile(r"\bELEVENLABS_[A-Z0-9_]+\b")
_ELEVENLABS_NAME_PATTERN = re.compile(r"eleven\s*labs|elevenlabs", re.IGNORECASE)


def redact_provider_message(message: Any) -> str:
    text = "" if message is None else str(message)
    if not text:
        return ""

    sanitized = _ELEVENLABS_URL_PATTERN.sub("<provider-api-url>", text)
    sanitized = _ELEVENLABS_HOST_PATTERN.sub("<provider-api-host>", sanitized)
    sanitized = _ELEVENLABS_ENV_PATTERN.sub("TRANSCRIPTION_PROVIDER_SETTING", sanitized)
    sanitized = _ELEVENLABS_NAME_PATTERN.sub(_PROVIDER_LABEL, sanitized)
    return sanitized


def redact_provider_payload(payload: Any, parent_key: str | None = None) -> Any:
    if isinstance(payload, Mapping):
        return {
            key: redact_provider_payload(value, parent_key=str(key).lower())
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_provider_payload(item, parent_key=parent_key) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_provider_payload(item, parent_key=parent_key) for item in payload)
    if isinstance(payload, str):
        if parent_key in _REDACT_TEXT_FIELDS:
            return payload
        return redact_provider_message(payload)
    return payload
