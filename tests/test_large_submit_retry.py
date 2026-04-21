import os
from pathlib import Path

import requests

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app import tasks as tasks_module


class _DummyResponse:
    def __init__(self, status_code: int, text: str, payload: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("No JSON")
        return self._payload


def _write_audio_file(path: Path) -> None:
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")


def _configure_retry_settings(monkeypatch) -> None:
    monkeypatch.setattr(tasks_module, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(tasks_module, "ELEVENLABS_STT_API_URL", "https://provider.example/v1/speech-to-text")
    monkeypatch.setattr(tasks_module, "ELEVENLABS_PROXY_URL", None)
    monkeypatch.setattr(tasks_module, "ELEVENLABS_WEBHOOK_ID", None)
    monkeypatch.setattr(tasks_module, "ELEVENLABS_LANGUAGE_CODE", "ru")
    monkeypatch.setattr(tasks_module, "LARGE_SUBMIT_MAX_RETRIES", 3)
    monkeypatch.setattr(tasks_module, "LARGE_SUBMIT_BACKOFF_SECONDS", 1.0)
    monkeypatch.setattr(tasks_module, "LARGE_SUBMIT_BACKOFF_MULTIPLIER", 2.0)
    monkeypatch.setattr(tasks_module, "LARGE_SUBMIT_MAX_BACKOFF_SECONDS", 5.0)


def test_submit_large_retries_on_network_error_then_succeeds(monkeypatch, tmp_path):
    _configure_retry_settings(monkeypatch)
    audio_path = tmp_path / "audio.wav"
    _write_audio_file(audio_path)

    calls = {"count": 0}
    sleeps = []
    mapping_calls = []
    update_calls = []

    def _mock_post(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.RequestException("Temporary failure in name resolution for api.elevenlabs.io")
        return _DummyResponse(200, '{"request_id":"req-123"}', {"request_id": "req-123"})

    def _mock_sleep(delay):
        sleeps.append(delay)

    def _mock_set_request_mapping(_redis, request_id, task_id, **_kwargs):
        mapping_calls.append((request_id, task_id))

    def _mock_update_large_task(_redis, task_id, updates, **_kwargs):
        update_calls.append((task_id, updates))
        return {"task_id": task_id}

    monkeypatch.setattr(tasks_module.requests, "post", _mock_post)
    monkeypatch.setattr(tasks_module.time, "sleep", _mock_sleep)
    monkeypatch.setattr(tasks_module, "set_request_mapping", _mock_set_request_mapping)
    monkeypatch.setattr(tasks_module, "update_large_task", _mock_update_large_task)

    result = tasks_module.submit_large_elevenlabs_task.run(
        str(audio_path),
        "task-1",
        "cb-token",
    )

    assert result == {"status": "submitted", "request_id": "req-123"}
    assert calls["count"] == 2
    assert sleeps == [1.0]
    assert mapping_calls == [("req-123", "task-1")]
    assert update_calls
    assert update_calls[-1][1]["status"] == "processing"
    assert not audio_path.exists()


def test_submit_large_retries_on_retryable_http_status(monkeypatch, tmp_path):
    _configure_retry_settings(monkeypatch)
    audio_path = tmp_path / "audio.wav"
    _write_audio_file(audio_path)

    calls = {"count": 0}
    sleeps = []

    def _mock_post(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _DummyResponse(503, "provider temporary unavailable")
        return _DummyResponse(200, '{"request_id":"req-456"}', {"request_id": "req-456"})

    monkeypatch.setattr(tasks_module.requests, "post", _mock_post)
    monkeypatch.setattr(tasks_module.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(tasks_module, "set_request_mapping", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks_module, "update_large_task", lambda *_args, **_kwargs: {"task_id": "task-2"})

    result = tasks_module.submit_large_elevenlabs_task.run(
        str(audio_path),
        "task-2",
        "cb-token",
    )

    assert result == {"status": "submitted", "request_id": "req-456"}
    assert calls["count"] == 2
    assert sleeps == [1.0]
    assert not audio_path.exists()


def test_submit_large_fails_after_max_network_retries(monkeypatch, tmp_path):
    _configure_retry_settings(monkeypatch)
    audio_path = tmp_path / "audio.wav"
    _write_audio_file(audio_path)

    sleeps = []
    captured_failure = {}

    def _mock_post(*_args, **_kwargs):
        raise requests.RequestException("api.elevenlabs.io dns failure")

    def _mock_mark_failed(task_id, error_message):
        captured_failure["task_id"] = task_id
        captured_failure["error"] = error_message

    monkeypatch.setattr(tasks_module.requests, "post", _mock_post)
    monkeypatch.setattr(tasks_module.time, "sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr(tasks_module, "_mark_large_task_failed", _mock_mark_failed)

    result = tasks_module.submit_large_elevenlabs_task.run(
        str(audio_path),
        "task-3",
        "cb-token",
    )

    assert result["status"] == "failed"
    assert "elevenlabs" not in result["error"].lower()
    assert captured_failure["task_id"] == "task-3"
    assert "elevenlabs" not in captured_failure["error"].lower()
    assert sleeps == [1.0, 2.0]
    assert not audio_path.exists()
