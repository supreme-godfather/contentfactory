import binascii
import logging
from pathlib import Path
from uuid import uuid4

import httpx

from .config import get_settings
from .provider_credentials import (
    FISH_AUDIO_MODELS,
    MINIMAX_SPEECH_MODELS,
    get_fish_audio_model_id,
    get_fish_audio_reference_id,
    get_minimax_speech_model,
    get_minimax_voice_id,
    get_provider_secret,
)

FISH_AUDIO_TTS_URL = "https://api.fish.audio/v1/tts"
MINIMAX_TTS_URL = "https://api.minimax.io/v1/t2a_v2"
FISH_AUDIO_ASR_URL = "https://api.fish.audio/v1/asr"

logger = logging.getLogger(__name__)


def _validated_text(text: str, *, maximum: int) -> str:
    value = text.strip()
    if not value:
        raise ValueError("Narration text cannot be empty.")
    if len(value) > maximum:
        raise ValueError(f"Narration text exceeds the provider limit of {maximum} characters.")
    return value


def _write_audio_atomically(destination: Path, audio: bytes) -> None:
    if len(audio) < 32:
        raise RuntimeError("The voice provider returned an empty or invalid audio file.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(audio)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def generate_fish_audio_voiceover(text: str, destination: Path) -> None:
    api_key = get_provider_secret("fish_audio")
    reference_id = get_fish_audio_reference_id()
    model_id = get_fish_audio_model_id()
    if not api_key:
        raise RuntimeError("Fish Audio is selected but no API key is configured.")
    if not reference_id:
        raise RuntimeError("Fish Audio requires a configured reference voice ID.")
    if model_id not in FISH_AUDIO_MODELS:
        raise RuntimeError("Fish Audio model configuration is invalid.")

    response = httpx.post(
        FISH_AUDIO_TTS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model_id,
        },
        json={
            "text": _validated_text(text, maximum=15000),
            "reference_id": reference_id,
            "format": "mp3",
            "sample_rate": 44100,
            "mp3_bitrate": 128,
            "latency": "normal",
            "chunk_length": 300,
            "normalize": True,
            "condition_on_previous_chunks": True,
            "prosody": {
                "speed": 0.98,
                "volume": 0,
                "normalize_loudness": True,
            },
            "features": ["quality-guard"],
        },
        timeout=180,
    )
    response.raise_for_status()
    _write_audio_atomically(destination, response.content)


def generate_minimax_voiceover(
    text: str,
    destination: Path,
    voice_profile: dict[str, float] | None = None,
) -> None:
    settings = get_settings()
    api_key = settings.minimax_api_key or get_provider_secret("minimax")
    voice_id = get_minimax_voice_id()
    model_id = get_minimax_speech_model()
    if not api_key:
        raise RuntimeError("MiniMax speech is selected but no Platform API key is configured.")
    if not voice_id:
        raise RuntimeError("MiniMax speech requires a configured voice ID.")
    if model_id not in MINIMAX_SPEECH_MODELS:
        raise RuntimeError("MiniMax speech model configuration is invalid.")

    profile = {"speed": 0.98, "vol": 1, "pitch": 0}
    for key in ("speed", "vol", "pitch"):
        value = (voice_profile or {}).get(key)
        if isinstance(value, (int, float)):
            profile[key] = float(value)
    profile["speed"] = max(0.5, min(2.0, round(profile["speed"], 2)))
    # MiniMax expects the volume and pitch as integers.
    profile["vol"] = int(max(1, min(2, round(profile["vol"]))))
    profile["pitch"] = int(max(-12, min(12, round(profile["pitch"]))))

    response = httpx.post(
        MINIMAX_TTS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_id,
            "text": _validated_text(text, maximum=9999),
            "stream": False,
            "language_boost": "Hindi",
            "text_normalization_mode": "quality",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": profile["speed"],
                "vol": profile["vol"],
                "pitch": profile["pitch"],
            },
            "audio_setting": {
                "sample_rate": 44100,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
            "subtitle_enable": False,
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    base_response = payload.get("base_resp") or {}
    if int(base_response.get("status_code") or 0) != 0:
        status_msg = str(base_response.get("status_msg") or "unknown error")
        if profile != {"speed": 0.98, "vol": 1, "pitch": 0} and voice_profile:
            # A tuned voice can be refused by the provider; the neutral voice is
            # always acceptable, so the reel is still produced.
            logger.warning(
                "MiniMax refused the tuned voice profile (%s); retrying neutral",
                status_msg,
            )
            generate_minimax_voiceover(text, destination)
            return
        raise RuntimeError(f"MiniMax speech generation was rejected: {status_msg}")
    encoded_audio = (payload.get("data") or {}).get("audio")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise RuntimeError("MiniMax speech returned no audio.")
    try:
        audio = bytes.fromhex(encoded_audio)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("MiniMax speech returned invalid audio data.") from exc
    _write_audio_atomically(destination, audio)


def transcribe_fish_audio(audio: bytes, filename: str = "clip-audio.mp3") -> dict[str, object]:
    api_key = get_provider_secret("fish_audio")
    if not api_key:
        raise RuntimeError("Fish Audio transcription requires a configured API key.")
    if not audio or len(audio) > 25 * 1024 * 1024:
        raise ValueError("Transcription audio must be between 1 byte and 25 MB.")
    response = httpx.post(
        FISH_AUDIO_ASR_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "model": "transcribe-1-pro",
        },
        data={
            "ignore_timestamps": "false",
            "tag_audio_events": "false",
            "diarize": "auto",
        },
        files={"audio": (Path(filename).name, audio, "audio/mpeg")},
        timeout=900,
    )
    response.raise_for_status()
    payload = response.json()
    segments = payload.get("segments") or []
    safe_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = max(0.0, float(segment.get("start")))
            end = max(start, float(segment.get("end")))
        except (TypeError, ValueError):
            continue
        text = " ".join(str(segment.get("text") or "").split())
        if text and end > start:
            safe_segments.append({"text": text, "start": start, "end": end})
    return {
        "text": " ".join(str(payload.get("text") or "").split()),
        "duration": float(payload.get("duration") or 0),
        "language_code": str(payload.get("language_code") or "")[:16],
        "segments": safe_segments,
    }
