from dataclasses import dataclass

from .config import get_settings


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


def get_publisher(platform: str) -> Publisher:
    settings = get_settings()
    if settings.mock_publishing:
        return MockPublisher(platform)
    return DisabledPublisher(platform)
