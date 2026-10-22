import base64
import json
from pathlib import Path

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .config import get_settings
from .provider_credentials import get_provider_secret


def _credentials():
    settings = get_settings()
    service_account_json = settings.google_service_account_json or get_provider_secret("google")
    if not service_account_json or not settings.google_cloud_project:
        raise RuntimeError(
            "Google media is enabled but GOOGLE_SERVICE_ACCOUNT_JSON and "
            "GOOGLE_CLOUD_PROJECT are not configured."
        )
    try:
        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid service-account JSON.") from exc
    credentials.refresh(Request())
    return credentials


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_credentials().token}"}


def generate_voiceover(text: str, destination: Path) -> None:
    settings = get_settings()
    response = httpx.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers=_headers(),
        json={
            "input": {"text": text[:4800]},
            "voice": {
                "languageCode": "hi-IN",
                "name": settings.google_tts_voice,
                "ssmlGender": "FEMALE",
            },
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.96},
        },
        timeout=120,
    )
    response.raise_for_status()
    encoded = response.json().get("audioContent")
    if not encoded:
        raise RuntimeError("Google Text-to-Speech returned no audio.")
    destination.write_bytes(base64.b64decode(encoded))


def generate_image(prompt: str, destination: Path) -> None:
    settings = get_settings()
    endpoint = (
        f"https://{settings.google_cloud_location}-aiplatform.googleapis.com/v1/"
        f"projects/{settings.google_cloud_project}/locations/{settings.google_cloud_location}/"
        f"publishers/google/models/{settings.google_image_model}:predict"
    )
    response = httpx.post(
        endpoint,
        headers=_headers(),
        json={
            "instances": [{"prompt": prompt[:1000]}],
            "parameters": {"sampleCount": 1, "aspectRatio": "9:16"},
        },
        timeout=180,
    )
    response.raise_for_status()
    predictions = response.json().get("predictions") or []
    encoded = predictions[0].get("bytesBase64Encoded") if predictions else None
    if not encoded:
        raise RuntimeError("Vertex AI Imagen returned no image.")
    destination.write_bytes(base64.b64decode(encoded))


def create_job_assets(job_id: str, topic: str, script: str, media_root: Path) -> dict[str, str]:
    settings = get_settings()
    from .provider_credentials import media_generation_enabled

    if not media_generation_enabled():
        return {}

    from .provider_credentials import provider_status
    selected = provider_status()["selected"]
    if selected == "openai":
        return _create_openai_assets(job_id, topic, script, media_root)
    if selected != "google":
        # The owner selected a provider whose generation endpoint is not
        # connected yet (key stored in Telegram). Never crash generation:
        # the job still gets its script, storyboard and render brief.
        import logging

        logging.getLogger(__name__).warning(
            "Provider %s is selected but not wired for media generation yet; skipping AI media",
            selected,
        )
        return {}
    media_root.mkdir(parents=True, exist_ok=True)
    voiceover = media_root / f"{job_id}-voiceover.mp3"
    image = media_root / f"{job_id}-scene-1.png"
    generate_voiceover(script, voiceover)
    generate_image(
        f"Original vertical documentary illustration about {topic}; Hindi educational explainer, "
        "cinematic lighting, clean composition, no text, no logos, no real person likeness.",
        image,
    )
    return {"voiceover": voiceover.name, "image": image.name}


def _create_openai_assets(job_id: str, topic: str, script: str, media_root: Path) -> dict[str, str]:
    api_key = get_provider_secret("openai")
    if not api_key:
        raise RuntimeError("OpenAI is selected but no OpenAI API key is configured.")
    headers = {"Authorization": f"Bearer {api_key}"}
    voiceover = media_root / f"{job_id}-voiceover.mp3"
    image = media_root / f"{job_id}-scene-1.png"
    audio = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers=headers,
        json={
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "input": script[:4096],
            "response_format": "mp3",
        },
        timeout=180,
    )
    audio.raise_for_status()
    voiceover.write_bytes(audio.content)
    artwork = httpx.post(
        "https://api.openai.com/v1/images/generations",
        headers=headers,
        json={
            "model": "gpt-image-1",
            "prompt": (
                f"Original vertical documentary illustration about {topic}; Hindi educational "
                "explainer, cinematic lighting, clean composition, no text, no logos."
            ),
            "size": "1024x1536",
            "quality": "medium",
        },
        timeout=240,
    )
    artwork.raise_for_status()
    encoded = (artwork.json().get("data") or [{}])[0].get("b64_json")
    if not encoded:
        raise RuntimeError("OpenAI image generation returned no image.")
    image.write_bytes(base64.b64decode(encoded))
    return {"voiceover": voiceover.name, "image": image.name}
