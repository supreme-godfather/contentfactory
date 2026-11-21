from unittest.mock import patch

import httpx


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_safe_fish_and_minimax_hindi_defaults_exist_without_keys():
    from app.provider_credentials import get_fish_audio_reference_id, get_minimax_voice_id
    from app.voice_catalog import discover_fish_hindi_voices, discover_minimax_hindi_voices

    with patch("app.voice_catalog.get_provider_secret", return_value=None):
        fish = discover_fish_hindi_voices()
        minimax = discover_minimax_hindi_voices()

    assert get_fish_audio_reference_id() == "4ebf5f78ef984caba1fbf70c8803f12b"
    assert get_minimax_voice_id() == "hindi_male_1_v2"
    assert any(option["voice_id"] == get_fish_audio_reference_id() for option in fish)
    assert any(option["voice_id"] == get_minimax_voice_id() for option in minimax)


def test_elevenlabs_catalog_only_returns_hindi_account_voices():
    from app.voice_catalog import discover_elevenlabs_hindi_voices

    payload = {
        "voices": [
            {
                "voice_id": "hindi_voice_1",
                "name": "Hindi Narrator",
                "is_owner": True,
                "verified_languages": [{"language": "hi", "locale": "hi-IN"}],
                "preview_url": "https://preview.elevenlabs.io/hindi.mp3",
            },
            {
                "voice_id": "english_voice",
                "name": "English",
                "labels": {"language": "en"},
            },
        ]
    }
    with (
        patch("app.voice_catalog.get_provider_secret", return_value="eleven-key"),
        patch("app.voice_catalog.httpx.get", return_value=FakeResponse(payload)) as request,
    ):
        voices = discover_elevenlabs_hindi_voices()

    assert [voice["voice_id"] for voice in voices] == ["hindi_voice_1"]
    assert voices[0]["owned"] is True
    assert request.call_args.kwargs["headers"]["xi-api-key"] == "eleven-key"


def test_minimax_catalog_validates_documented_system_ids():
    from app.voice_catalog import discover_minimax_hindi_voices

    response = FakeResponse({
        "system_voice": [
            {"voice_id": "hindi_male_1_v2"},
            {"voice_id": "English_Persuasive_Man"},
        ],
        "base_resp": {"status_code": 0, "status_msg": "success"},
    })
    with (
        patch("app.voice_catalog.get_provider_secret", return_value="minimax-key"),
        patch("app.voice_catalog.httpx.post", return_value=response),
    ):
        voices = discover_minimax_hindi_voices()

    assert [voice["voice_id"] for voice in voices] == ["hindi_male_1_v2"]


def test_elevenlabs_model_setting_defaults_to_current_turbo():
    from app.provider_credentials import get_elevenlabs_tts_model, set_elevenlabs_tts_model

    assert get_elevenlabs_tts_model() == "eleven_v4_turbo"
    set_elevenlabs_tts_model("eleven_v4")
    assert get_elevenlabs_tts_model() == "eleven_v4"
