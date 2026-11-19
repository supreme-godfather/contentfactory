import base64
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .config import get_settings
from .provider_credentials import get_provider_secret

logger = logging.getLogger(__name__)


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
    from .hq import get_reel_voice

    voice_name = get_reel_voice()
    response = httpx.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers=_headers(),
        json={
            "input": {"text": text[:4800]},
            "voice": {
                "languageCode": "hi-IN",
                "name": voice_name or settings.google_tts_voice,
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


def generate_edge_tts_voiceover(text: str, destination: Path) -> None:
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "edge_tts",
                "-v",
                "hi-IN-MadhurNeural",
                "-t",
                text[:4800],
                "--write-media",
                str(destination),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Microsoft Edge TTS generation failed.") from exc


def generate_elevenlabs_voiceover(text: str, destination: Path) -> None:
    from .provider_credentials import get_elevenlabs_voice_id

    api_key = get_provider_secret("elevenlabs")
    voice_id = get_elevenlabs_voice_id()
    if not api_key or not voice_id:
        raise RuntimeError("ElevenLabs API key and voice ID must both be configured.")
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text[:4800],
            "model_id": "eleven_multilingual_v2",
        },
        timeout=180,
    )
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("ElevenLabs returned an empty audio response.")
    destination.write_bytes(response.content)


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


def generate_huggingface_image(prompt: str, destination: Path) -> None:
    from .provider_credentials import get_huggingface_image_endpoint

    token = get_provider_secret("huggingface")
    endpoint = get_huggingface_image_endpoint()
    if not token:
        raise RuntimeError("Hugging Face image generation requires a configured access token.")
    if not endpoint:
        raise RuntimeError("Hugging Face image generation requires an Inference Endpoint URL.")

    for attempt in range(4):
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "width": 768,
                    "height": 1360,
                    "num_inference_steps": 4,
                },
            },
            timeout=180,
        )
        if response.status_code not in {502, 503, 504} or attempt == 3:
            break
        time.sleep(5 * (2**attempt))
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in {"image/png", "image/jpeg", "image/webp"} or not response.content:
        raise RuntimeError("Hugging Face endpoint did not return a supported image.")
    destination.write_bytes(response.content)


def generate_minimax_video(prompt: str, destination: Path) -> None:
    settings = get_settings()
    api_key = settings.minimax_api_key or get_provider_secret("minimax")
    if not api_key:
        raise RuntimeError("MiniMax video generation requires a configured API key.")

    base_url = settings.minimax_base_url.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._/-]*)?", base_url):
        raise RuntimeError("MINIMAX_BASE_URL must be an HTTPS API base URL.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    submitted = httpx.post(
        f"{base_url}/video_generation",
        headers=headers,
        json={
            "model": settings.minimax_model,
            "content": [{"type": "text", "text": prompt[:2000]}],
            "duration": max(5, min(10, settings.minimax_duration_seconds)),
            "ratio": "9:16",
        },
        timeout=180,
    )
    submitted.raise_for_status()
    task_id = submitted.json().get("task_id")
    if not task_id:
        raise RuntimeError("MiniMax did not return a task_id.")

    for attempt in range(60):
        polled = httpx.get(
            f"{base_url}/query/video_generation/{task_id}",
            headers=headers,
            timeout=180,
        )
        polled.raise_for_status()
        task = polled.json().get("task") or {}
        status = str(task.get("status") or "").lower()
        if status == "succeeded":
            video_url = (task.get("content") or {}).get("url")
            if not video_url:
                raise RuntimeError("MiniMax returned success without a video URL.")
            downloaded = httpx.get(video_url, timeout=180)
            downloaded.raise_for_status()
            if not downloaded.content:
                raise RuntimeError("MiniMax returned an empty video.")
            destination.write_bytes(downloaded.content)
            return
        if status in {"failed", "cancelled"}:
            error = task.get("error") or task.get("base_resp") or "unknown error"
            raise RuntimeError(f"MiniMax video task failed: {error}")
        time.sleep(10 if attempt < 30 else 15)
    raise RuntimeError("MiniMax video task timed out while waiting for completion.")


def create_job_assets(
    job_id: str,
    topic: str,
    script: str,
    media_root: Path,
    storyboard: list[dict[str, object]] | None = None,
) -> dict[str, str]:
    from .provider_credentials import (
        get_provider_for_role,
        media_generation_enabled,
        no_paid_providers_enabled,
        provider_status,
    )

    if not media_generation_enabled():
        return {}

    media_root.mkdir(parents=True, exist_ok=True)
    assets: dict[str, str] = {}
    image_provider = get_provider_for_role("image")
    if image_provider == "huggingface":
        if no_paid_providers_enabled():
            raise RuntimeError(
                "FLUX image generation is blocked while no-paid mode is ON. "
                "Disable no-paid mode to use the configured GPU endpoint."
            )
        if not provider_status().get("huggingface"):
            raise RuntimeError("FLUX image generation requires a Hugging Face access token.")
        from .provider_credentials import get_huggingface_image_endpoint

        if not get_huggingface_image_endpoint():
            raise RuntimeError("FLUX image generation requires a Hugging Face Inference Endpoint URL.")
        scene_prompts = _huggingface_scene_prompts(topic, script, storyboard or [])
        generated_images = []
        try:
            for index, prompt in enumerate(scene_prompts, start=1):
                image = media_root / f"{job_id}-scene-{index}.png"
                generated_images.append(image)
                try:
                    generate_huggingface_image(prompt, image)
                except httpx.HTTPStatusError as exc:
                    from .provider_credentials import (
                        is_provider_quota_error,
                        mark_provider_quota_error,
                    )

                    if is_provider_quota_error(exc):
                        until = mark_provider_quota_error("huggingface", exc)
                        logger.warning(
                            "Hugging Face quota/rate limit reached; cooling down until %s",
                            until.isoformat(),
                        )
                    raise
                assets[f"scene_{index}"] = image.name
        except Exception:
            for image in generated_images:
                image.unlink(missing_ok=True)
            raise
    elif get_provider_for_role("video") == "minimax":
        if no_paid_providers_enabled():
            raise RuntimeError(
                "MiniMax video generation is blocked while no-paid mode is ON."
            )
        if not provider_status().get("minimax"):
            raise RuntimeError("MiniMax video generation requires a configured API key.")
        scene_prompts = _huggingface_scene_prompts(topic, script, storyboard or [])
        generated_videos = []
        try:
            for index, prompt in enumerate(scene_prompts, start=1):
                video = media_root / f"{job_id}-scene-video-{index}.mp4"
                generated_videos.append(video)
                generate_minimax_video(prompt, video)
                assets[f"scene_video_{index}"] = video.name
        except Exception:
            for video in generated_videos:
                video.unlink(missing_ok=True)
            raise
    else:
        image = media_root / f"{job_id}-scene-1.png"
        if _generate_role_asset("image", job_id, topic, script, image):
            assets["image"] = image.name
    voiceover = media_root / f"{job_id}-voiceover.mp3"
    if _generate_role_asset("voice", job_id, topic, script, voiceover):
        assets["voiceover"] = voiceover.name
    return assets


def _huggingface_scene_prompts(
    topic: str,
    script: str,
    storyboard: list[dict[str, object]],
) -> list[str]:
    scenes = storyboard or [
        {"stage": f"scene {index}", "visual_direction": ""}
        for index in range(1, 7)
    ]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", script) if part.strip()]
    if len(paragraphs) < len(scenes):
        sentences = [part.strip() for part in re.split(r"(?<=[.!?।])\s+", script) if part.strip()]
        paragraphs = [
            " ".join(sentences[index * len(sentences) // len(scenes):(index + 1) * len(sentences) // len(scenes)])
            for index in range(len(scenes))
        ]
    prompts = []
    for index, scene in enumerate(scenes):
        direction = str(scene.get("visual_direction") or "").strip()
        narration = paragraphs[index] if index < len(paragraphs) else script.strip()
        prompts.append(
            f"Create one original 9:16 vertical cinematic documentary frame about {topic}. "
            f"Scene beat: {scene.get('stage', f'scene {index + 1}')}. "
            f"Visual direction: {direction}. Narration context: {narration[:500]}. "
            "Make this scene visually distinct and specific to the narration. "
            "Premium editorial photography, natural dramatic lighting, coherent realistic details, "
            "deep charcoal shadows with restrained muted-gold accents, clear focal subject, "
            "leave lower 20 percent uncluttered for subtitles. No text, letters, logos, watermarks, "
            "collages, repeated layouts, recognizable real people, or copied creator style."
        )
    return prompts


def _generate_role_asset(
    role: str,
    job_id: str,
    topic: str,
    script: str,
    destination: Path,
) -> bool:
    from .provider_credentials import (
        is_provider_quota_error,
        mark_provider_quota_error,
        provider_candidates_for_role,
    )

    candidates = provider_candidates_for_role(role)
    for index, provider in enumerate(candidates):
        try:
            if provider == "google":
                if role == "image":
                    generate_image(_image_prompt(topic), destination)
                else:
                    generate_voiceover(script, destination)
            elif provider == "openai":
                assets = _create_openai_assets(
                    job_id,
                    topic,
                    script,
                    destination.parent,
                    generate_image=role == "image",
                    generate_voice=role == "voice",
                )
                if not assets:
                    return False
            elif provider == "edge_tts" and role == "voice":
                generate_edge_tts_voiceover(script, destination)
            elif provider == "elevenlabs" and role == "voice":
                generate_elevenlabs_voiceover(script, destination)
            elif provider == "off":
                return False
            else:
                continue
            return True
        except httpx.HTTPStatusError as exc:
            if not is_provider_quota_error(exc):
                raise
            until = mark_provider_quota_error(provider, exc)
            logger.warning(
                "%s quota/rate limit reached; cooling down until %s",
                provider,
                until.isoformat(),
            )
            if index + 1 == len(candidates):
                logger.warning("No configured fallback provider remains for %s generation", role)
        except RuntimeError as exc:
            if provider != "edge_tts":
                raise
            logger.warning("Edge TTS was unavailable (%s)", type(exc).__name__)
            raise
    return False


def _image_prompt(topic: str) -> str:
    return (
        f"Original vertical dark-cinematic documentary artwork about {topic}; faceless symbolic "
        "psychology/history imagery, charcoal black with muted gold and restrained deep red, "
        "dramatic side lighting, high contrast, uncluttered composition, no text, no logos, "
        "no real person likeness, no copied creator style."
    )


def _create_openai_assets(
    job_id: str,
    topic: str,
    script: str,
    media_root: Path,
    *,
    generate_image: bool = True,
    generate_voice: bool = True,
) -> dict[str, str]:
    api_key = get_provider_secret("openai")
    if not api_key:
        raise RuntimeError("OpenAI is selected but no OpenAI API key is configured.")
    headers = {"Authorization": f"Bearer {api_key}"}
    voiceover = media_root / f"{job_id}-voiceover.mp3"
    image = media_root / f"{job_id}-scene-1.png"
    assets: dict[str, str] = {}
    if generate_voice:
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
        assets["voiceover"] = voiceover.name
    if generate_image:
        artwork = httpx.post(
            "https://api.openai.com/v1/images/generations",
            headers=headers,
            json={
                "model": "gpt-image-2.5-flare",
                "prompt": _image_prompt(topic),
                "size": "1024x1792",
                "quality": "medium",
            },
            timeout=240,
        )
        artwork.raise_for_status()
        encoded = (artwork.json().get("data") or [{}])[0].get("b64_json")
        if not encoded:
            raise RuntimeError("OpenAI image generation returned no image.")
        image.write_bytes(base64.b64decode(encoded))
        assets["image"] = image.name
    return assets
