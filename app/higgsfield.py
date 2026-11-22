"""Higgsfield provider: Kling, Seedance, Wan and SOUL through one async API.

Higgsfield is a front door to the best video models (Kling, Seedance, Wan,
Happy Horse, LTX, PixVerse, Grok) and to SOUL, its character-consistent image
model. Everything uses the same lifecycle: submit a request, get a
``status_url``, poll it, download the result.

Two things matter for this factory:

- **References must be public URLs.** The engine already serves every media
  file from ``PUBLIC_BASE_URL/media/<file>``, so a character sheet or a line of
  dialogue can be handed straight to the model.
- **Higgsfield bills in credits, not seconds.** Every call records the credits
  it spent so the owner can see the balance before a batch of reels is made.
"""
import logging
import time
import uuid
from pathlib import Path

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.higgsfield.ai"

# Endpoint ids, taken from the Higgsfield model reference.
SEEDANCE_REFERENCE = "bytedance/seedance-2.0/reference-to-video"
SEEDANCE_IMAGE = "bytedance/seedance-2.0/image-to-video"
SEEDANCE_TEXT = "bytedance/seedance-2.0/text-to-video"
WAN_TEXT = "wan/v2.6/text-to-video"
WAN_IMAGE = "wan/v2.6/image-to-video"
WAN_REFERENCE = "wan/v2.6/reference-to-video"
KLING_IMAGE = "kling-video/v2.6/pro/image-to-video"
KLING_TEXT = "kling-video/v2.6/pro/text-to-video"
SOUL_IMAGE = "higgsfield-ai/soul/v2/standard"

# Estimated credit cost per generation, used for the ledger only.
CREDIT_COSTS: dict[str, int] = {
    SEEDANCE_REFERENCE: 23,
    SEEDANCE_IMAGE: 23,
    SEEDANCE_TEXT: 23,
    WAN_TEXT: 12,
    WAN_IMAGE: 12,
    WAN_REFERENCE: 12,
    KLING_IMAGE: 10,
    KLING_TEXT: 10,
    SOUL_IMAGE: 4,
}


class HiggsfieldError(RuntimeError):
    """A Higgsfield request could not be completed."""


# Published API rates in US dollars per second of output. The estimate
# endpoint is authoritative for the owner's account; these are the fallback.
MODEL_USD_PER_SECOND: dict[str, float] = {
    SEEDANCE_REFERENCE: 0.099,
    SEEDANCE_IMAGE: 0.080,
    SEEDANCE_TEXT: 0.080,
    WAN_TEXT: 0.038,
    WAN_IMAGE: 0.038,
    WAN_REFERENCE: 0.038,
    KLING_IMAGE: 0.107,
    KLING_TEXT: 0.071,
}

IMAGE_USD: dict[str, float] = {
    SOUL_IMAGE: 0.0032,
}


def estimate_cost(endpoint: str, payload: dict) -> float:
    """Ask Higgsfield what one request will cost before it is made.

    The estimate endpoint uses the same body as the generation endpoint and
    answers with the authoritative price for the authenticated account, so the
    factory can decide whether a shot fits the budget before it spends.
    """
    try:
        response = httpx.post(
            f"{base_url()}/estimate/{endpoint}",
            headers=_headers(),
            json=payload,
            timeout=60,
        )
        if response.status_code >= 400:
            raise HiggsfieldError(
                f"Higgsfield could not estimate {endpoint}: HTTP {response.status_code}"
            )
        raw = response.json().get("usd")
        return float(raw)
    except (TypeError, ValueError):
        pass
    except httpx.HTTPStatusError:
        raise
    seconds = int(payload.get("duration") or 5)
    if endpoint in IMAGE_USD:
        return IMAGE_USD[endpoint]
    rate = MODEL_USD_PER_SECOND.get(endpoint, 0.1)
    return round(rate * seconds, 4)


def _credentials() -> tuple[str, str]:
    from .provider_credentials import get_provider_secret

    credentials = get_provider_secret("higgsfield")
    if not credentials:
        raise HiggsfieldError("Higgsfield needs an API key in the provider panel.")
    if credentials.count(":") != 1:
        raise HiggsfieldError(
            "The Higgsfield API key must use the Key ID:Key Secret format."
        )
    key_id, key_secret = credentials.split(":", 1)
    if not key_id.strip() or not key_secret.strip():
        raise HiggsfieldError("The Higgsfield API key must include both a key ID and a key secret.")
    return key_id.strip(), key_secret.strip()


def base_url() -> str:
    settings = get_settings()
    return (settings.higgsfield_base_url or DEFAULT_BASE_URL).rstrip("/")


def public_media_url(filename: str) -> str | None:
    """The public URL of a media file, which Higgsfield needs for references."""
    if Path(filename).name != filename:
        return None
    settings = get_settings()
    if not settings.public_base_url:
        return None
    return f"{settings.public_base_url.rstrip('/')}/media/{filename}"


def credit_cost(endpoint: str, *, resolution: str = "720p", seconds: int = 5) -> float:
    """Fallback price estimate in US dollars when the estimate call fails."""
    if endpoint in IMAGE_USD:
        return IMAGE_USD[endpoint]
    rate = MODEL_USD_PER_SECOND.get(endpoint, 0.1)
    if resolution in {"1080p", "4k"}:
        rate *= 2.2
    return round(rate * max(1, int(round(seconds / 5))), 4)


def _headers() -> dict[str, str]:
    key_id, key_secret = _credentials()
    return {
        "Authorization": f"Key {key_id}:{key_secret}",
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _submit(endpoint: str, payload: dict) -> str:
    response = httpx.post(
        f"{base_url()}/{endpoint}",
        headers=_headers(),
        json=payload,
        timeout=180,
    )
    if response.status_code >= 400:
        detail = response.text[:600]
        raise HiggsfieldError(
            f"Higgsfield rejected {endpoint} with HTTP {response.status_code}: {detail}"
        )
    submission = response.json()
    status_url = submission.get("status_url")
    if not status_url:
        raise HiggsfieldError("Higgsfield did not return a status_url.")
    return str(status_url)


def _poll(status_url: str, *, timeout_seconds: int = 900) -> dict:
    deadline = time.time() + timeout_seconds
    delay = 8
    headers = _headers()
    while time.time() < deadline:
        response = httpx.get(status_url, headers=headers, timeout=180)
        response.raise_for_status()
        result = response.json()
        status = str(result.get("status") or "").lower()
        if status == "completed":
            return result
        if status in {"failed", "nsfw", "canceled", "cancelled", "error"}:
            raise HiggsfieldError(f"Higgsfield request ended with status {status}.")
        time.sleep(delay)
        delay = min(20, delay + 2)
    raise HiggsfieldError("Higgsfield request timed out.")


def _download(url: str, destination: Path) -> None:
    downloaded = httpx.get(url, timeout=300, follow_redirects=True)
    downloaded.raise_for_status()
    if not downloaded.content:
        raise HiggsfieldError("Higgsfield returned an empty file.")
    destination.write_bytes(downloaded.content)


def _result_url(result: dict) -> str:
    video = result.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])
    images = result.get("images") or result.get("image")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        url = images[0].get("url")
        if url:
            return str(url)
    if isinstance(images, dict) and images.get("url"):
        return str(images["url"])
    raise HiggsfieldError("Higgsfield finished without a file URL.")


def generate_video(
    endpoint: str,
    payload: dict,
    destination: Path,
    *,
    timeout_seconds: int = 900,
) -> tuple[float, float]:
    """Run one Higgsfield video request.

    Returns ``(estimated_usd, spent_usd)`` so the caller can write the real
    cost into the ledger.
    """
    estimated = estimate_cost(endpoint, payload)
    status_url = _submit(endpoint, payload)
    result = _poll(status_url, timeout_seconds=timeout_seconds)
    _download(_result_url(result), destination)
    seconds = int(payload.get("duration") or 5)
    resolution = str(payload.get("resolution") or "720p")
    spent = round(MODEL_USD_PER_SECOND.get(endpoint, 0.1) * seconds, 4)
    return estimated, spent


def generate_image(
    endpoint: str,
    payload: dict,
    destination: Path,
    *,
    timeout_seconds: int = 600,
) -> tuple[float, float]:
    """Run one Higgsfield image request and return (estimated, spent) in USD."""
    estimated = estimate_cost(endpoint, payload)
    status_url = _submit(endpoint, payload)
    result = _poll(status_url, timeout_seconds=timeout_seconds)
    _download(_result_url(result), destination)
    return estimated, IMAGE_USD.get(endpoint, 0.0032)


def seedance_reference_video(
    prompt: str,
    destination: Path,
    *,
    image_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    duration: int = 5,
    resolution: str = "720p",
    aspect_ratio: str = "9:16",
    generate_audio: bool = True,
    timeout_seconds: int = 900,
) -> tuple[float, float]:
    """Image (and optional audio) references into a vertical video.

    This is the speaking-character path: the character image keeps the face
    and the dialogue audio drives the mouth.
    """
    payload: dict[str, object] = {
        "prompt": prompt[:4000],
        "duration": max(4, min(15, int(duration))),
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    }
    if image_urls:
        payload["image_urls"] = image_urls[:9]
    if audio_urls:
        payload["audio_urls"] = audio_urls[:3]
    if video_urls:
        payload["video_urls"] = video_urls[:3]
    if not image_urls and not video_urls:
        raise HiggsfieldError("Seedance reference-to-video needs an image or video reference.")
    return generate_video(
        SEEDANCE_REFERENCE, payload, destination, timeout_seconds=timeout_seconds
    )


def wan_video(
    prompt: str,
    destination: Path,
    *,
    audio_url: str | None = None,
    duration: int = 5,
    resolution: str = "720p",
    prompt_extend: bool = True,
    timeout_seconds: int = 900,
) -> tuple[float, float]:
    """Text (and optional audio reference) into a video."""
    payload: dict[str, object] = {
        "prompt": prompt[:4000],
        "duration": 5 if duration not in {5, 10, 15} else duration,
        "resolution": resolution if resolution in {"720p", "1080p"} else "720p",
        "prompt_extend": prompt_extend,
    }
    if audio_url:
        payload["audio_url"] = audio_url
    return generate_video(WAN_TEXT, payload, destination, timeout_seconds=timeout_seconds)


def kling_video(
    prompt: str,
    destination: Path,
    *,
    image_url: str | None = None,
    duration: int = 5,
    resolution: str = "720p",
    generate_audio: bool = True,
    timeout_seconds: int = 900,
) -> tuple[float, float]:
    """Kling image-to-video with optional native sound."""
    endpoint = KLING_IMAGE if image_url else KLING_TEXT
    payload: dict[str, object] = {
        "prompt": prompt[:4000],
        "duration": max(5, min(10, int(duration))),
        "resolution": resolution if resolution in {"720p", "1080p"} else "720p",
    }
    if image_url:
        payload["image_url"] = image_url
    if generate_audio:
        payload["generate_audio"] = True
    return generate_video(endpoint, payload, destination, timeout_seconds=timeout_seconds)


def soul_image(
    prompt: str,
    destination: Path,
    *,
    reference_urls: list[str] | None = None,
    aspect_ratio: str = "9:16",
    timeout_seconds: int = 600,
) -> tuple[float, float]:
    """A SOUL character image, optionally guided by reference images."""
    payload: dict[str, object] = {
        "prompt": prompt[:3000],
        "aspect_ratio": aspect_ratio,
    }
    if reference_urls:
        payload["image_urls"] = reference_urls[:4]
    return generate_image(SOUL_IMAGE, payload, destination, timeout_seconds=timeout_seconds)
