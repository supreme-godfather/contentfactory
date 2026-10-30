from dataclasses import dataclass
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .platform_credentials import get_platform_secret, get_platform_value
from .youtube_oauth import get_valid_access_token

# Instagram container polling — module constants so tests can speed them up.
INSTAGRAM_POLL_INTERVAL_SECONDS = 3
INSTAGRAM_MAX_POLLS = 10


@dataclass(frozen=True)
class PublicationResult:
    status: str
    remote_id: str | None = None
    remote_url: str | None = None
    detail: str = ""


class Publisher:
    platform: str

    def publish(self, *, title: str, description: str, media_path: str) -> PublicationResult:
        raise NotImplementedError


class MockPublisher(Publisher):
    def __init__(self, platform: str):
        self.platform = platform

    def publish(self, *, title: str, description: str, media_path: str) -> PublicationResult:
        return PublicationResult(
            status="published",
            remote_id=f"mock-{self.platform}-{abs(hash(media_path))}",
            remote_url=f"https://example.invalid/{self.platform}/{abs(hash(media_path))}",
            detail="Mock publication only; no platform API was called.",
        )


class DisabledPublisher(Publisher):
    def __init__(self, platform: str):
        self.platform = platform

    def publish(self, *, title: str, description: str, media_path: str) -> PublicationResult:
        return PublicationResult(
            status="blocked",
            detail=f"{self.platform} publishing is disabled until official authorization is configured.",
        )


class YouTubePublisher(Publisher):
    platform = "youtube"

    def publish(self, *, title: str, description: str, media_path: str) -> PublicationResult:
        path = Path(media_path)
        if not path.is_file():
            return PublicationResult(status="blocked", detail="Rendered media is not available on the API service.")
        if path.stat().st_size < 1024:
            return PublicationResult(status="blocked", detail="Rendered media is too small to publish.")
        settings = get_settings()
        try:
            token = get_valid_access_token()
            metadata = {
                "snippet": {
                    "title": title[:100],
                    "description": description[:5000],
                    "categoryId": "27",
                },
                "status": {
                    "privacyStatus": settings.youtube_privacy_status
                    if settings.youtube_privacy_status in {"private", "unlisted", "public"}
                    else "private",
                    "selfDeclaredMadeForKids": False,
                },
            }
            with httpx.Client(timeout=30) as client:
                start = client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"part": "snippet,status", "uploadType": "resumable"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=UTF-8",
                        "X-Upload-Content-Type": "video/mp4",
                        "X-Upload-Content-Length": str(path.stat().st_size),
                    },
                    content=json.dumps(metadata),
                )
                start.raise_for_status()
                upload_url = start.headers.get("Location")
                if not upload_url:
                    return PublicationResult(status="blocked", detail="YouTube did not provide an upload URL.")
                with path.open("rb") as media:
                    response = None
                    while True:
                        chunk = media.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        response = client.put(
                            upload_url,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "video/mp4",
                                "Content-Length": str(len(chunk)),
                            },
                            content=chunk,
                        )
                        if response.status_code not in {200, 201, 308}:
                            response.raise_for_status()
                        if response.status_code in {200, 201}:
                            break
                if response is None or response.status_code not in {200, 201}:
                    return PublicationResult(status="blocked", detail="YouTube upload did not complete.")
                payload = response.json()
            video_id = payload.get("id")
            if not video_id:
                return PublicationResult(status="blocked", detail="YouTube returned no video ID.")
            return PublicationResult(
                status="published",
                remote_id=video_id,
                remote_url=f"https://youtu.be/{video_id}",
                detail="Published through the official YouTube Data API.",
            )
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            return PublicationResult(status="blocked", detail=f"YouTube publication failed: {str(exc)[:300]}")


class InstagramPublisher(Publisher):
    """Publish a Reel via the official Meta Graph API.

    Safe/blocked by default: requires INSTAGRAM_ENABLED plus a configured
    Instagram user id and access token, and — because the Graph API fetches
    the video itself — a publicly accessible http(s) video URL. A local
    render file on this server cannot be uploaded directly.
    """

    platform = "instagram"

    def publish(self, *, title: str, description: str, media_path: str) -> PublicationResult:
        settings = get_settings()
        access_token = get_platform_secret("instagram") or settings.instagram_access_token
        user_id = get_platform_value("instagram", "user_id") or settings.instagram_user_id
        if not user_id or not user_id.isdecimal() or not access_token:
            return PublicationResult(
                status="blocked",
                detail="Instagram account ID or access token is missing or invalid.",
            )
        if media_path and not media_path.startswith(("http://", "https://")):
            filename = Path(media_path).name
            if settings.public_base_url and filename.endswith(".mp4"):
                media_path = f"{settings.public_base_url.rstrip('/')}/media/{filename}"
        public_base = settings.public_base_url.rstrip("/")
        public_url = urlparse(media_path) if media_path else None
        expected_host = urlparse(public_base).netloc if public_base else ""
        if (
            not media_path
            or public_url is None
            or public_url.scheme != "https"
            or not expected_host
            or public_url.netloc != expected_host
            or not public_url.path.startswith("/media/")
        ):
            return PublicationResult(
                status="blocked",
                detail=(
                    "Instagram requires the completed MP4 at this factory's HTTPS /media route. "
                    "Configure PUBLIC_BASE_URL and persistent media storage before publishing."
                ),
            )
        version = settings.instagram_graph_version or "v21.0"
        base = f"https://graph.facebook.com/{version}"
        token = access_token
        caption = f"{title}\n\n{description}"[:2200]
        try:
            with httpx.Client(timeout=60) as client:
                create = client.post(
                    f"{base}/{user_id}/media",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "media_type": "REELS",
                        "video_url": media_path,
                        "caption": caption,
                    },
                )
                create.raise_for_status()
                container_id = create.json().get("id")
                if not container_id:
                    return PublicationResult(status="blocked", detail="Instagram returned no media container ID.")
                # Wait for the container to finish processing.
                finished = False
                for _ in range(max(1, INSTAGRAM_MAX_POLLS)):
                    status_resp = client.get(
                        f"{base}/{container_id}",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"fields": "status_code"},
                    )
                    status_resp.raise_for_status()
                    container_status = status_resp.json().get("status_code")
                    if container_status == "FINISHED":
                        finished = True
                        break
                    if container_status == "ERROR":
                        return PublicationResult(
                            status="blocked",
                            detail="Instagram could not process this Reel. Check codec, dimensions, and duration.",
                        )
                    if INSTAGRAM_POLL_INTERVAL_SECONDS > 0:
                        time.sleep(INSTAGRAM_POLL_INTERVAL_SECONDS)
                if not finished:
                    return PublicationResult(status="blocked", detail="Instagram media container did not finish processing in time.")
                publish_resp = client.post(
                    f"{base}/{user_id}/media_publish",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"creation_id": container_id},
                )
                publish_resp.raise_for_status()
                remote_id = publish_resp.json().get("id")
            if not remote_id:
                return PublicationResult(status="blocked", detail="Instagram returned no published media ID.")
            return PublicationResult(
                status="published",
                remote_id=str(remote_id),
                remote_url=f"https://www.instagram.com/reel/{remote_id}/",
                detail="Published through the official Meta Graph API.",
            )
        except httpx.HTTPError as exc:
            return PublicationResult(status="blocked", detail=f"Instagram publication failed: {str(exc)[:300]}")


def get_publisher(platform: str) -> Publisher:
    from .hq import is_mock_publishing

    if is_mock_publishing():
        return MockPublisher(platform)
    settings = get_settings()
    if platform == "instagram":
        from .platform_credentials import platform_enabled

        if platform_enabled("instagram", settings.instagram_enabled):
            return InstagramPublisher()
    if platform == "youtube" and settings.youtube_enabled:
        return YouTubePublisher()
    return DisabledPublisher(platform)
