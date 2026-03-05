import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.api.v1.endpoints import transcribation as transcribation_module


def _build_headers(raw_body: bytes, timestamp: int, secret: str, extra_headers: dict | None = None) -> dict:
    signed_payload = f"{timestamp}.".encode() + raw_body
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    headers = {
        "x-relay-signature": signature,
        "x-relay-timestamp": str(timestamp),
        "content-type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(transcribation_module.settings, "downstream_hmac_secret", "relay-secret")
    monkeypatch.setattr(transcribation_module.settings, "downstream_hmac_header", "x-relay-signature")
    monkeypatch.setattr(transcribation_module.settings, "downstream_timestamp_header", "x-relay-timestamp")
    monkeypatch.setattr(transcribation_module.settings, "relay_timestamp_tolerance_seconds", 300)

    monkeypatch.setattr(
        transcribation_module,
        "get_large_task",
        lambda *_args, **_kwargs: {
            "task_id": "task-1",
            "callback_token": "cb-token",
            "client_webhook_url": None,
            "stream_id": "stream-1",
            "is_finished": True,
            "elevenlabs_request_id": None,
        },
    )
    monkeypatch.setattr(transcribation_module, "get_task_id_by_request_id", lambda *_args, **_kwargs: "task-1")
    monkeypatch.setattr(
        transcribation_module,
        "update_large_task",
        lambda *_args, **_kwargs: {
            "task_id": "task-1",
            "client_webhook_url": None,
            "stream_id": "stream-1",
            "is_finished": True,
        },
    )
    monkeypatch.setattr(transcribation_module, "send_webhook_with_retries", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(transcribation_module, "_remember_relay_event_once", lambda *_args, **_kwargs: True)

    app = FastAPI()
    app.add_api_route("/webhooks/elevenlabs", transcribation_module.receive_elevenlabs_webhook, methods=["POST"])
    return TestClient(app)


def _base_payload() -> dict:
    return {
        "webhook_metadata": {"task_id": "task-1", "callback_token": "cb-token"},
        "text": "test transcript",
        "words": [{"speaker_id": "1"}],
    }


def test_valid_signature_returns_success(client):
    payload = _base_payload()
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["state"] == "completed"


def test_valid_relay_envelope_returns_success(client):
    payload = {
        "event_id": "evt-1",
        "received_at": "2026-03-04T21:42:04Z",
        "source_signature_valid": True,
        "correlation_id": "corr-1",
        "payload": _base_payload(),
    }
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["state"] == "completed"


def test_relay_envelope_with_nested_transcription_and_request_id_mapping(client, monkeypatch):
    captured = {}

    def _capture_update(_redis_client, _task_id, updates, **_kwargs):
        captured.update(updates)
        return {
            "task_id": "task-1",
            "client_webhook_url": None,
            "stream_id": "stream-1",
            "is_finished": True,
        }

    monkeypatch.setattr(transcribation_module, "update_large_task", _capture_update)

    payload = {
        "event_id": "evt-3",
        "received_at": "2026-03-04T21:42:04Z",
        "source_signature_valid": True,
        "correlation_id": "corr-3",
        "payload": {
            "type": "speech_to_text_transcription",
            "data": {
                "request_id": "req-1",
                "webhook_metadata": None,
                "transcription": {
                    "text": "hello world",
                    "words": [
                        {"speaker_id": "speaker_0"},
                        {"speaker_id": "speaker_1"},
                    ],
                },
            },
        },
    }
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["state"] == "completed"
    assert captured["status"] == "completed"
    assert captured["text"] == "hello world"
    assert captured["speaker_count"] == 2


def test_invalid_signature_returns_401(client):
    payload = _base_payload()
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "wrong-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid relay signature"}


def test_modified_body_with_same_signature_returns_401(client):
    original_payload = _base_payload()
    signed_body = json.dumps(original_payload).encode()
    headers = _build_headers(signed_body, int(time.time()), "relay-secret")

    tampered_payload = _base_payload()
    tampered_payload["text"] = "tampered"
    tampered_body = json.dumps(tampered_payload).encode()

    response = client.post("/webhooks/elevenlabs", content=tampered_body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid relay signature"}


def test_stale_timestamp_returns_401(client):
    payload = _base_payload()
    raw_body = json.dumps(payload).encode()
    old_timestamp = int(time.time()) - 1000
    headers = _build_headers(raw_body, old_timestamp, "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid relay signature"}


def test_missing_signature_headers_returns_401(client):
    payload = _base_payload()
    raw_body = json.dumps(payload).encode()

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers={"content-type": "application/json"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid relay signature"}


def test_duplicate_event_id_is_ignored_without_business_processing(client, monkeypatch):
    update_called = {"value": False}

    def _update_should_not_be_called(*_args, **_kwargs):
        update_called["value"] = True
        return {}

    monkeypatch.setattr(transcribation_module, "_remember_relay_event_once", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(transcribation_module, "update_large_task", _update_should_not_be_called)

    payload = _base_payload()
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(
        raw_body,
        int(time.time()),
        "relay-secret",
        extra_headers={"x-relay-event-id": "event-42"},
    )

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "duplicate_event"}
    assert update_called["value"] is False


def test_invalid_relay_envelope_payload_type_returns_400(client):
    payload = {
        "event_id": "evt-2",
        "payload": "not-an-object",
    }
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid webhook payload"}


def test_no_client_webhook_configured_skips_downstream_send(client, monkeypatch):
    called = {"value": False}

    def _should_not_send(*_args, **_kwargs):
        called["value"] = True
        return {"status": "success"}

    monkeypatch.setattr(transcribation_module, "send_webhook_with_retries", _should_not_send)

    payload = {
        "event_id": "evt-no-webhook-1",
        "received_at": "2026-03-04T21:42:04Z",
        "source_signature_valid": True,
        "correlation_id": "corr-no-webhook-1",
        "payload": _base_payload(),
    }
    raw_body = json.dumps(payload).encode()
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["value"] is False


def test_downstream_receives_raw_relay_payload(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        transcribation_module,
        "get_large_task",
        lambda *_args, **_kwargs: {
            "task_id": "task-1",
            "callback_token": "cb-token",
            "client_webhook_url": "https://client.example/webhook",
            "stream_id": "stream-1",
            "is_finished": True,
            "elevenlabs_request_id": None,
        },
    )
    monkeypatch.setattr(
        transcribation_module,
        "update_large_task",
        lambda *_args, **_kwargs: {
            "task_id": "task-1",
            "client_webhook_url": "https://client.example/webhook",
            "stream_id": "stream-1",
            "is_finished": True,
        },
    )

    def _capture_send(url, result, task_id, raw_payload=None):
        captured["url"] = url
        captured["result"] = result
        captured["task_id"] = task_id
        captured["raw_payload"] = raw_payload
        return {"status": "success"}

    monkeypatch.setattr(transcribation_module, "send_webhook_with_retries", _capture_send)

    payload = {
        "event_id": "evt-raw-1",
        "received_at": "2026-03-04T21:42:04Z",
        "source_signature_valid": True,
        "correlation_id": "corr-raw-1",
        "payload": _base_payload(),
    }
    raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = _build_headers(raw_body, int(time.time()), "relay-secret")

    response = client.post("/webhooks/elevenlabs", content=raw_body, headers=headers)

    assert response.status_code == 200
    assert captured["url"] == "https://client.example/webhook"
    assert captured["task_id"] == "task-1"
    assert captured["result"] == payload
    assert captured["raw_payload"] == raw_body
