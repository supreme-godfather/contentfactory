import base64
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .config import get_settings
from .provider_credentials import get_provider_secret
from .voice_providers import generate_fish_audio_voiceover, generate_minimax_voiceover

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
    from .captions import character_alignment_to_words, words_to_caption_cues
    from .provider_credentials import get_elevenlabs_tts_model, get_elevenlabs_voice_id

    api_key = get_provider_secret("elevenlabs")
    voice_id = get_elevenlabs_voice_id()
    if not api_key or not voice_id:
        raise RuntimeError("ElevenLabs API key and voice ID must both be configured.")
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        params={"output_format": "mp3_44100_128"},
        json={
            "text": text[:4800],
            "model_id": get_elevenlabs_tts_model(),
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    encoded_audio = payload.get("audio_base64")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise RuntimeError("ElevenLabs returned no timestamped audio.")
    destination.write_bytes(base64.b64decode(encoded_audio))
    alignment = payload.get("normalized_alignment") or payload.get("alignment") or {}
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if characters and starts and ends:
        words = character_alignment_to_words(characters, starts, ends)
        cues = words_to_caption_cues(words)
        destination.with_suffix(".timing.json").write_text(
            json.dumps(
                {
                    "provider": "elevenlabs",
                    "timing_source": "provider_alignment",
                    "caption_cues": cues,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


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


def generate_minimax_image(
    prompt: str,
    destination: Path,
    subject_reference_url: str | None = None,
) -> None:
    """Generate one image with MiniMax image-01.

    The image route lives on the v1 path while video generation uses the
    configured base, so both are tried. A public character-sheet URL can be
    passed as a subject reference, which keeps the same character in every
    generated frame.
    """
    settings = get_settings()
    api_key = settings.minimax_api_key or get_provider_secret("minimax")
    if not api_key:
        raise RuntimeError("MiniMax image generation requires a configured API key.")
    base_url = settings.minimax_base_url.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._/-]*)?", base_url):
        raise RuntimeError("MINIMAX_BASE_URL must be an HTTPS API base URL.")
    root = re.sub(r"/v\d+$", "", base_url)
    endpoints = [f"{root}/v1/image_generation"]
    if f"{base_url}/image_generation" not in endpoints:
        endpoints.append(f"{base_url}/image_generation")
    payload: dict[str, object] = {
        "model": "image-01",
        "prompt": prompt[:1500],
        "aspect_ratio": "9:16",
        "n": 1,
    }
    if subject_reference_url:
        payload["subject_reference"] = [
            {"type": "character", "image_file": subject_reference_url}
        ]
    last_error = ""
    for endpoint in endpoints:
        response = httpx.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=240,
        )
        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code}: {response.text[:400]}"
            continue
        payload = response.json()
        base_resp = payload.get("base_resp") or {}
        if int(base_resp.get("status_code") or 0) != 0:
            last_error = str(base_resp.get("status_msg") or "the provider rejected the request")
            continue
        urls = (payload.get("data") or {}).get("image_urls") or []
        if not urls:
            last_error = "the provider returned no image URL"
            continue
        downloaded = httpx.get(str(urls[0]), timeout=180, follow_redirects=True)
        downloaded.raise_for_status()
        if not downloaded.content:
            raise RuntimeError("MiniMax returned an empty image.")
        destination.write_bytes(downloaded.content)
        return
    raise RuntimeError(f"MiniMax image generation failed: {last_error}")


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{encoded}"


def generate_minimax_video(
    prompt: str,
    destination: Path,
    first_frame: Path | None = None,
) -> None:
    settings = get_settings()
    # H3 video consumes the pay-as-you-go Platform API key only. The M Plan
    # subscription key is intentionally kept separate for text/research APIs.
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
    content: list[dict[str, object]] = [{"type": "text", "text": prompt[:2000]}]
    payload: dict[str, object] = {
        "model": settings.minimax_model,
        "content": content,
        "duration": max(5, min(10, settings.minimax_duration_seconds)),
        "resolution": "768P",
    }
    if first_frame is not None and first_frame.is_file():
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(first_frame)},
            }
        )
    else:
        payload["ratio"] = "9:16"

    submitted = httpx.post(
        f"{base_url}/video_generation",
        headers=headers,
        json=payload,
        timeout=180,
    )
    if submitted.status_code >= 400 and "content" in content:
        # The image reference form may not be accepted for this model: fall back
        # to text-to-video instead of failing the whole render.
        detail = submitted.text[:800].replace(api_key, "[REDACTED]")
        logger.warning(
            "MiniMax rejected the reference image (%s); retrying text-to-video: %s",
            submitted.status_code,
            detail[:200],
        )
        content = [item for item in content if item.get("type") == "text"]
        payload.pop("ratio", None)
        payload["ratio"] = "9:16"
        submitted = httpx.post(
            f"{base_url}/video_generation",
            headers=headers,
            json=payload,
            timeout=180,
        )
    if submitted.status_code >= 400:
        detail = submitted.text[:1500].replace(api_key, "[REDACTED]")
        raise RuntimeError(
            f"MiniMax video_generation returned HTTP {submitted.status_code}: {detail}"
        )
    submitted.raise_for_status()
    task_id = submitted.json().get("task_id")
    if not task_id:
        raise RuntimeError("MiniMax did not return a task_id.")
    logger.info("MiniMax video task %s submitted for %s", task_id, destination.name)

    polls = 0
    for attempt in range(40):
        polls += 1
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
            logger.info(
                "MiniMax video task %s finished after %s polls (%s)",
                task_id,
                polls,
                destination.name,
            )
            return
        if status in {"failed", "cancelled"}:
            error = task.get("error") or task.get("base_resp") or "unknown error"
            raise RuntimeError(f"MiniMax video task failed: {error}")
        time.sleep(10 if attempt < 30 else 15)
    raise RuntimeError(
        f"MiniMax video task {task_id} did not finish in time ({polls} polls)."
    )


def generate_higgsfield_video(prompt: str, destination: Path) -> None:
    settings = get_settings()
    credentials = get_provider_secret("higgsfield")
    if not credentials:
        raise RuntimeError("Higgsfield video generation requires an API key.")
    if credentials.count(":") != 1:
        raise RuntimeError("Higgsfield API key must use the Key ID:Key Secret format.")
    key_id, key_secret = credentials.split(":", 1)
    if not key_id.strip() or not key_secret.strip():
        raise RuntimeError("Higgsfield API key must include both a key ID and key secret.")

    base_url = settings.higgsfield_base_url.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._/-]*)?", base_url):
        raise RuntimeError("HIGGSFIELD_BASE_URL must be an HTTPS API base URL.")

    headers = {
        "Authorization": f"Key {key_id.strip()}:{key_secret.strip()}",
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }
    submitted = httpx.post(
        f"{base_url}/wan/v2.6/text-to-video",
        headers=headers,
        json={
            "prompt": prompt[:2000],
            "duration": 5,
            "resolution": "720p",
            "prompt_extend": True,
        },
        timeout=180,
    )
    submitted.raise_for_status()
    submission = submitted.json()
    status_url = submission.get("status_url")
    if not status_url:
        raise RuntimeError("Higgsfield did not return a status_url.")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._/-]*)?", status_url):
        raise RuntimeError("Higgsfield returned an invalid status_url.")

    for attempt in range(60):
        polled = httpx.get(status_url, headers={"Authorization": headers["Authorization"]}, timeout=180)
        polled.raise_for_status()
        result = polled.json()
        status = str(result.get("status") or "").lower()
        if status == "completed":
            video_url = (result.get("video") or {}).get("url")
            if not video_url:
                raise RuntimeError("Higgsfield returned success without a video URL.")
            downloaded = httpx.get(video_url, timeout=180)
            downloaded.raise_for_status()
            if not downloaded.content:
                raise RuntimeError("Higgsfield returned an empty video.")
            destination.write_bytes(downloaded.content)
            return
        if status in {"failed", "nsfw", "canceled"}:
            raise RuntimeError(f"Higgsfield video task ended with status: {status}")
        time.sleep(10 if attempt < 30 else 15)
    raise RuntimeError("Higgsfield video task timed out while waiting for completion.")


def create_job_assets(
    job_id: str,
    topic: str,
    script: str,
    media_root: Path,
    storyboard: list[dict[str, object]] | None = None,
    thumbnail_prompt: str | None = None,
    custom_voiceover: str | None = None,
    character_reference: Path | None = None,
    character_prompt: str | None = None,
    voice_profile: dict[str, float] | None = None,
    dialogue_lines: list[dict[str, object]] | None = None,
    character_sheets: dict[str, str] | None = None,
    video_scene_count: int = 2,
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
    video_provider = get_provider_for_role("video")
    scene_prompts = _huggingface_scene_prompts(topic, script, storyboard or [])
    template = _scene_template_from_storyboard(storyboard or [])
    if template:
        scene_prompts = _huggingface_scene_prompts(topic, script, storyboard or [], template)
    if character_prompt:
        scene_prompts = [
            f"{prompt}. {character_prompt[:600]}" for prompt in scene_prompts
        ]
    generated: list[Path] = []
    try:
        # The narration is produced first: the Higgsfield reference workflows
        # need the dialogue audio, and a missing voice must not cost a video.
        voiceover = media_root / f"{job_id}-voiceover.mp3"
        voiceover.with_suffix(".timing.json").unlink(missing_ok=True)
        owner_voice = media_root / custom_voiceover if custom_voiceover else None
        if (
            owner_voice is not None
            and owner_voice.is_file()
            and Path(custom_voiceover).name == custom_voiceover
        ):
            assets["voiceover"] = owner_voice.name
        elif dialogue_lines:
            # Every line is spoken by its own character, with its own voice.
            parts = _render_dialogue_parts(
                job_id,
                dialogue_lines,
                media_root,
                voice_profile,
            )
            if parts:
                for index, name in enumerate(parts, start=1):
                    assets[f"dialogue_part_{index}"] = name
        elif _generate_role_asset("voice", job_id, topic, script, voiceover, voice_profile=voice_profile):
            assets["voiceover"] = voiceover.name
            timing = voiceover.with_suffix(".timing.json")
            if timing.is_file():
                assets["voice_timing"] = timing.name
            from .cost import get_unit_prices, record_usage

            voice_provider = get_provider_for_role("voice")
            prices = get_unit_prices()
            voice_price = {
                "minimax": prices["minimax_speech_per_1k_chars"],
                "elevenlabs": prices["elevenlabs_per_1k_chars"],
                "fish_audio": prices["fish_tts_per_1k_chars"],
                "google": prices["google_speech_per_1k_chars"],
                "openai": prices["openai_speech_per_1k_chars"],
            }.get(voice_provider, 0.0)
            record_usage(
                job_id,
                voice_provider,
                "speech",
                max(0, len(script)) / 1000,
                "1k_chars",
                voice_price,
            )

        # Expensive provider video is reserved for the hook and the payoff beat.
        video_scenes = (1, 5) if len(scene_prompts) >= 5 else (1,)
        if max(1, int(video_scene_count)) >= 3 and len(scene_prompts) >= 5:
            video_scenes = (1, 3, 5)
        elif max(1, int(video_scene_count)) >= 4 and len(scene_prompts) >= 7:
            video_scenes = (1, 3, 5, 7)
        if video_provider == "minimax":
            if no_paid_providers_enabled():
                raise RuntimeError(
                    "MiniMax video generation is blocked while no-paid mode is ON."
                )
            if not provider_status().get("minimax"):
                raise RuntimeError("MiniMax video generation requires a configured API key.")
            from .cost import get_unit_prices, record_usage

            price = get_unit_prices()["minimax_video_per_second"]

            def _make_minimax(index: int) -> tuple[str, str]:
                video = media_root / f"{job_id}-scene-video-{index}.mp4"
                generate_minimax_video(
                    scene_prompts[index - 1],
                    video,
                    first_frame=character_reference,
                )
                return f"scene_video_{index}", video.name

            results = _parallel_map(_make_minimax, video_scenes)
            for key, name in results:
                assets[key] = name
                record_usage(job_id, "minimax", "video", 5, "second", price)
        elif video_provider.startswith("higgsfield"):
            if no_paid_providers_enabled():
                raise RuntimeError(
                    "Higgsfield video generation is blocked while no-paid mode is ON."
                )
            if not provider_status().get("higgsfield"):
                raise RuntimeError("Higgsfield video generation requires a configured API key.")
            from .cost import get_unit_prices, record_usage

            _generate_higgsfield_scenes(
                job_id,
                video_provider,
                scene_prompts,
                video_scenes,
                media_root,
                assets,
                generated,
                character_reference,
                assets.get("voiceover"),
                record_usage,
            )

        if image_provider != "off":
            from .cost import get_unit_prices, record_usage

            prices = get_unit_prices()
            image_price = {
                "minimax": prices["minimax_image"],
                "google": prices["google_image"],
                "openai": prices["openai_image"],
                "huggingface": prices["minimax_image"],
            }.get(image_provider, 0.0)
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
                    raise RuntimeError(
                        "FLUX image generation requires a Hugging Face Inference Endpoint URL."
                    )
            for index, prompt in enumerate(scene_prompts, start=1):
                if f"scene_video_{index}" in assets:
                    continue
                image = media_root / f"{job_id}-scene-{index}.png"
                generated.append(image)
                reference_url = None
                if character_sheets:
                    from . import higgsfield

                    lead = character_sheets.get("lead")
                    if isinstance(lead, str) and lead:
                        reference_url = higgsfield.public_media_url(lead)
                if reference_url:
                    try:
                        generate_minimax_image(
                            prompt,
                            image,
                            subject_reference_url=reference_url,
                        )
                        assets[f"scene_{index}"] = image.name
                        record_usage(job_id, "minimax", "image", 1, "image", image_price)
                        continue
                    except Exception:
                        logger.warning(
                            "Character-consistent scene %s could not be generated",
                            index,
                            exc_info=True,
                        )
                        image.unlink(missing_ok=True)
                if image_provider == "huggingface":
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
                    record_usage(job_id, image_provider, "image", 1, "image", image_price)
                    continue
                if _generate_role_asset(
                    "image",
                    job_id,
                    topic,
                    script,
                    image,
                    prompt_override=prompt,
                ):
                    assets[f"scene_{index}"] = image.name
                    record_usage(job_id, image_provider, "image", 1, "image", image_price)
                else:
                    image.unlink(missing_ok=True)
    except Exception:
        for path in generated:
            path.unlink(missing_ok=True)
        raise

    if character_reference is not None and character_reference.is_file():
        # Any scene the image providers could not produce falls back to the
        # character sheet, so the character stays the same in every shot.
        for index in range(1, len(scene_prompts) + 1):
            if f"scene_video_{index}" in assets or f"scene_{index}" in assets:
                continue
            image = media_root / f"{job_id}-scene-{index}.jpg"
            generated.append(image)
            shutil.copyfile(character_reference, image)
            assets[f"scene_{index}"] = image.name

    if thumbnail_prompt:
        thumbnail = media_root / f"{job_id}-thumbnail.png"
        if _generate_role_asset(
            "image",
            job_id,
            topic,
            script,
            thumbnail,
            prompt_override=thumbnail_prompt,
        ):
            assets["thumbnail"] = thumbnail.name
            from .cost import get_unit_prices, record_usage

            prices = get_unit_prices()
            record_usage(
                job_id,
                image_provider,
                "thumbnail",
                1,
                "image",
                prices.get(f"{image_provider}_image", 0.0) if image_provider != "minimax" else prices["minimax_image"],
            )
    return assets


def _render_dialogue_parts(
    job_id: str,
    dialogue_lines: list[dict[str, object]],
    media_root: Path,
    voice_profile: dict[str, float] | None,
) -> list[str]:
    """Speak every dialogue line with its own character's voice."""
    from .characters import get_character
    from .voice_providers import generate_minimax_voiceover

    parts: list[str] = []
    for index, line in enumerate(dialogue_lines, start=1):
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        character_id = str(line.get("character_id") or "")
        character = get_character(character_id) if character_id else None
        profile = dict(character.voice_profile) if character else dict(voice_profile or {})
        destination = media_root / f"{job_id}-line-{index}.mp3"
        try:
            generate_minimax_voiceover(text, destination, profile)
        except Exception:
            logger.warning("Dialogue line %s could not be spoken", index, exc_info=True)
            continue
        parts.append(destination.name)
    return parts


def _parallel_map(function, items, workers: int = 3) -> list:
    """Run a small set of independent generation calls side by side.

    Provider video generation takes minutes per clip, so the scenes of one reel
    are produced concurrently instead of one after the other.
    """
    from concurrent.futures import ThreadPoolExecutor

    items = list(items)
    if len(items) <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(function, items))


def _generate_higgsfield_scenes(
    job_id: str,
    video_provider: str,
    scene_prompts: list[str],
    video_scenes: tuple[int, ...],
    media_root: Path,
    assets: dict[str, str],
    generated: list[Path],
    character_reference: Path | None,
    voiceover_name: str | None,
    record_usage,
) -> None:
    """Generate the video scenes through Higgsfield and record the credits.

    Four workflows are supported:

    - ``higgsfield`` — Wan 2.6 text-to-video (no reference).
    - ``higgsfield_seedance`` — Seedance 2.0 with the character image and the
      dialogue audio as references: the character speaks the line.
    - ``higgsfield_wan_audio`` — Wan 2.6 with the dialogue audio as reference.
    - ``higgsfield_kling`` — Kling 2.6 with native sound, no voice-over needed.
    """
    from . import higgsfield
    from .credits import affordable_shots, money_summary

    character_url = None
    if character_reference is not None and character_reference.is_file():
        character_url = higgsfield.public_media_url(character_reference.name)
    voice_url = None
    if voiceover_name:
        voice_url = higgsfield.public_media_url(voiceover_name)

    endpoint = {
        "higgsfield_seedance": higgsfield.SEEDANCE_REFERENCE,
        "higgsfield_wan_audio": higgsfield.WAN_TEXT,
        "higgsfield_kling": higgsfield.KLING_IMAGE,
    }.get(video_provider, higgsfield.WAN_TEXT)

    # The balance decides how many shots this reel can have: the factory spends
    # what it has instead of failing half way through a story.
    summary = money_summary()
    remaining = float(summary.get("remaining_usd") or 0.0)
    price_per_shot = higgsfield.credit_cost(endpoint, resolution="720p", seconds=5)
    affordable = affordable_shots(remaining, price_per_shot, len(video_scenes))
    if affordable < len(video_scenes):
        logger.warning(
            "The Higgsfield balance only pays for %s of %s shots at $%.2f each",
            affordable,
            len(video_scenes),
            price_per_shot,
        )
        video_scenes = tuple(video_scenes[:affordable])

    for index in video_scenes:
        if index > len(scene_prompts):
            continue
        prompt = scene_prompts[index - 1]
        video = media_root / f"{job_id}-scene-video-{index}.mp4"
        generated.append(video)
        if video_provider == "higgsfield_seedance":
            if not character_url:
                raise RuntimeError(
                    "The Seedance workflow needs a public character image reference."
                )
            estimated, spent = higgsfield.seedance_reference_video(
                prompt,
                video,
                image_urls=[character_url],
                audio_urls=[voice_url] if voice_url else None,
                duration=5,
                resolution="720p",
                aspect_ratio="9:16",
            )
        elif video_provider == "higgsfield_wan_audio":
            if not voice_url:
                raise RuntimeError(
                    "The Wan audio workflow needs a public dialogue audio reference."
                )
            estimated, spent = higgsfield.wan_video(
                prompt,
                video,
                audio_url=voice_url,
                duration=5,
                resolution="720p",
            )
        elif video_provider == "higgsfield_kling":
            estimated, spent = higgsfield.kling_video(
                prompt,
                video,
                image_url=character_url,
                duration=5,
                resolution="720p",
                generate_audio=True,
            )
        else:
            estimated, spent = higgsfield.wan_video(
                prompt,
                video,
                duration=5,
                resolution="720p",
            )
        assets[f"scene_video_{index}"] = video.name
        record_usage(job_id, "higgsfield", "video", 1, "request", spent)


def _scene_template_from_storyboard(storyboard: list[dict[str, object]]) -> dict[str, str] | None:
    """The scene template attached to a job, if it has one."""
    from .scene_templates import template_for

    for scene in storyboard:
        template_id = scene.get("scene_template")
        if isinstance(template_id, str) and template_id:
            template = template_for(template_id)
            if template is not None:
                return {
                    "prompt": template.prompt,
                    "camera": template.camera,
                    "sound": template.sound,
                    "location": template.location,
                }
    return None


def _huggingface_scene_prompts(
    topic: str,
    script: str,
    storyboard: list[dict[str, object]],
    template: dict[str, str] | None = None,
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
        if template and template.get("prompt"):
            prompts.append(
                f"{template['prompt']}. "
                f"Camera: {template.get('camera', '')}. "
                f"Sound: {template.get('sound', '')}. "
                f"Spoken line: {narration[:300]}. "
                "One continuous shot, photorealistic cinematic film, no text, no "
                "watermark, no logo, vertical 9:16, leave the lower fifth clear."
            )
            continue
        prompts.append(
            f"Create one original 9:16 vertical cinematic documentary frame about {topic}. "
            f"Scene beat: {scene.get('stage', f'scene {index + 1}')}. "
            f"Visual direction: {direction}. Narration context: {narration[:500]}. "
            "Make this scene visually distinct and specific to the narration. "
            "Use a smooth slow push-in or lateral tracking shot, strong subject hierarchy, "
            "high retention composition, and a single clear visual reveal instead of a montage. "
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
    prompt_override: str | None = None,
    voice_profile: dict[str, float] | None = None,
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
                    generate_image(prompt_override or _image_prompt(topic), destination)
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
                    image_prompt=prompt_override,
                )
                if not assets:
                    return False
            elif provider == "huggingface" and role == "image":
                generate_huggingface_image(
                    prompt_override or _image_prompt(topic),
                    destination,
                )
            elif provider == "minimax" and role == "image":
                generate_minimax_image(
                    prompt_override or _image_prompt(topic),
                    destination,
                )
            elif provider == "elevenlabs" and role == "voice":
                generate_elevenlabs_voiceover(script, destination)
            elif provider == "fish_audio" and role == "voice":
                generate_fish_audio_voiceover(script, destination)
            elif provider == "minimax" and role == "voice":
                generate_minimax_voiceover(script, destination, voice_profile)
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
        except RuntimeError:
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
