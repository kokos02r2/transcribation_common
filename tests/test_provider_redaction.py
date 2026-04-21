from app.utils.provider_redaction import redact_provider_message, redact_provider_payload


def test_redact_provider_message_replaces_vendor_url_and_env():
    message = (
        "ElevenLabs request failed at https://api.elevenlabs.io/v1/speech-to-text "
        "because ELEVENLABS_API_KEY is missing"
    )
    redacted = redact_provider_message(message)

    assert "ElevenLabs" not in redacted
    assert "elevenlabs" not in redacted.lower()
    assert "transcription provider" in redacted
    assert "<provider-api-url>" in redacted
    assert "TRANSCRIPTION_PROVIDER_SETTING" in redacted


def test_redact_provider_payload_keeps_transcript_text():
    payload = {
        "text": "В записи звучит слово ElevenLabs",
        "error_message": "ElevenLabs timeout",
        "nested": {
            "message": "api.elevenlabs.io is unavailable",
        },
    }

    redacted = redact_provider_payload(payload)

    assert redacted["text"] == payload["text"]
    assert redacted["error_message"] == "transcription provider timeout"
    assert redacted["nested"]["message"] == "<provider-api-host> is unavailable"
