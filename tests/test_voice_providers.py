from unittest.mock import patch

import httpx
import pytest


def test_fish_audio_tts_uses_configured_model_and_reference(tmp_path):
    from app.voice_providers import generate_fish_audio_voiceover

    response = httpx.Response(
        200,
        content=b"I" * 1024,
        request=httpx.Request("POST", "https://api.fish.audio/v1/tts"),
    )
    destination = tmp_path / "fish.mp3"
    with (
        patch("app.voice_providers.get_provider_secret", return_value="fish-secret"),
        patch("app.voice_providers.get_fish_audio_reference_id", return_value="voice_123"),
        patch("app.voice_providers.get_fish_audio_model_id", return_value="s2.1-pro"),
        patch("app.voice_providers.httpx.post", return_value=response) as request,
    ):
        generate_fish_audio_voiceover("यह एक पेशेवर हिन्दी आवाज़ परीक्षण है।", destination)

    assert destination.read_bytes() == b"I" * 1024
    assert request.call_args.args[0] == "https://api.fish.audio/v1/tts"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer fish-secret"
    assert request.call_args.kwargs["headers"]["model"] == "s2.1-pro"
    assert request.call_args.kwargs["json"]["reference_id"] == "voice_123"
    assert request.call_args.kwargs["json"]["prosody"]["speed"] == 0.98


def test_fish_audio_rejects_missing_reference(tmp_path):
    from app.voice_providers import generate_fish_audio_voiceover

    with (
        patch("app.voice_providers.get_provider_secret", return_value="fish-secret"),
        patch("app.voice_providers.get_fish_audio_reference_id", return_value=""),
    ):
        with pytest.raises(RuntimeError, match="reference voice ID"):
            generate_fish_audio_voiceover("हिन्दी", tmp_path / "fish.mp3")


def test_minimax_tts_decodes_hex_audio(tmp_path):
    from app.voice_providers import generate_minimax_voiceover

    audio = b"M" * 1024
    response = httpx.Response(
        200,
        json={
            "data": {"audio": audio.hex(), "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        },
        request=httpx.Request("POST", "https://api.minimax.io/v1/t2a_v2"),
    )
    destination = tmp_path / "minimax.mp3"
    with (
        patch("app.voice_providers.get_provider_secret", return_value="minimax-secret"),
        patch("app.voice_providers.get_minimax_voice_id", return_value="Hindi_Narrator"),
        patch("app.voice_providers.get_minimax_speech_model", return_value="speech-2.8-turbo"),
        patch("app.voice_providers.httpx.post", return_value=response) as request,
    ):
        generate_minimax_voiceover("यह एक पेशेवर हिन्दी आवाज़ परीक्षण है।", destination)

    assert destination.read_bytes() == audio
    payload = request.call_args.kwargs["json"]
    assert payload["model"] == "speech-2.8-turbo"
    assert payload["language_boost"] == "Hindi"
    assert payload["voice_setting"]["voice_id"] == "Hindi_Narrator"


def test_fish_and_minimax_voice_settings_are_validated():
    from app.provider_credentials import (
        get_fish_audio_model_id,
        get_fish_audio_reference_id,
        get_minimax_speech_model,
        get_minimax_voice_id,
        set_fish_audio_model_id,
        set_fish_audio_reference_id,
        set_minimax_speech_model,
        set_minimax_voice_id,
    )

    set_fish_audio_reference_id("fish_voice-123")
    set_fish_audio_model_id("s2.1-pro")
    set_minimax_voice_id("Hindi_Narrator")
    set_minimax_speech_model("speech-2.8-hd")

    assert get_fish_audio_reference_id() == "fish_voice-123"
    assert get_fish_audio_model_id() == "s2.1-pro"
    assert get_minimax_voice_id() == "Hindi_Narrator"
    assert get_minimax_speech_model() == "speech-2.8-hd"

    with pytest.raises(ValueError, match="Unsupported Fish Audio model"):
        set_fish_audio_model_id("future-model")
    with pytest.raises(ValueError, match="Unsupported MiniMax speech model"):
        set_minimax_speech_model("future-model")


def test_fish_audio_transcription_returns_sanitized_timestamps():
    from app.voice_providers import transcribe_fish_audio

    response = httpx.Response(
        200,
        json={
            "text": "  hello   world ",
            "duration": 1.2,
            "language_code": "en",
            "segments": [
                {"text": " hello ", "start": 0.0, "end": 0.5},
                {"text": "world", "start": 0.6, "end": 1.1},
            ],
        },
        request=httpx.Request("POST", "https://api.fish.audio/v1/asr"),
    )
    with (
        patch("app.voice_providers.get_provider_secret", return_value="fish-secret"),
        patch("app.voice_providers.httpx.post", return_value=response) as request,
    ):
        result = transcribe_fish_audio(b"audio" * 100)

    assert result["text"] == "hello world"
    assert result["segments"] == [
        {"text": "hello", "start": 0.0, "end": 0.5},
        {"text": "world", "start": 0.6, "end": 1.1},
    ]
    assert request.call_args.kwargs["headers"]["model"] == "transcribe-1-pro"
    assert request.call_args.kwargs["data"]["ignore_timestamps"] == "false"
