from dataclasses import dataclass
import json
from pathlib import Path

import httpx

from .config import get_settings
from .youtube_oauth import get_valid_access_token


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


def get_publisher(platform: str) -> Publisher:
    settings = get_settings()
    if settings.mock_publishing:
        return MockPublisher(platform)
    if platform == "youtube" and settings.youtube_enabled:
        return YouTubePublisher()
    return DisabledPublisher(platform)
