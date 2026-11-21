import json


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, headers=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_youtube_publisher_uploads_tags_and_custom_thumbnail(tmp_path, monkeypatch):
    import httpx

    from app.config import get_settings

    get_settings.cache_clear()
    media = tmp_path / "video.mp4"
    media.write_bytes(b"v" * 2048)
    thumbnail = tmp_path / "cover.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xff" + b"i" * 2048)
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            if url.endswith("/videos"):
                return FakeResponse(headers={"Location": "https://upload.example/session"})
            if url.endswith("/thumbnails/set"):
                return FakeResponse({"items": []})
            raise AssertionError(f"Unexpected POST {url}")

        def put(self, url, **kwargs):
            calls.append(("put", url, kwargs))
            return FakeResponse({"id": "video-id"}, status_code=201)

        def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            return FakeResponse({"items": [{"processingDetails": {"processingStatus": "succeeded"}}]})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr("app.publishers.get_valid_access_token", lambda: "test-token")

    from app.publishers import YouTubePublisher

    result = YouTubePublisher().publish(
        title="Hindi topic",
        description="A clear description #Hindi",
        media_path=str(media),
        tags=["Hindi", "Facts"],
        thumbnail_path=str(thumbnail),
    )

    assert result.status == "published"
    assert result.remote_id == "video-id"
    start_call = next(call for call in calls if call[0] == "post" and call[1].endswith("/videos"))
    upload_metadata = json.loads(start_call[2]["content"])
    assert upload_metadata["snippet"]["tags"] == ["Hindi", "Facts"]
    processing_call = next(
        call for call in calls
        if call[0] == "get" and call[1].endswith("/youtube/v3/videos")
    )
    assert processing_call[2]["headers"]["Authorization"] == "Bearer test-token"
    thumbnail_call = next(call for call in calls if call[0] == "post" and call[1].endswith("/thumbnails/set"))
    assert thumbnail_call[2]["params"]["videoId"] == "video-id"
    assert thumbnail_call[2]["headers"]["Authorization"] == "Bearer test-token"
    assert thumbnail_call[2]["content"] == thumbnail.read_bytes()
    get_settings.cache_clear()


def test_instagram_publisher_sends_caption_and_cover_url(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://factory.example")
    from app.config import get_settings

    get_settings.cache_clear()
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/media"):
                return FakeResponse({"id": "container-id"})
            return FakeResponse({"id": "published-id"})

        def get(self, url, **kwargs):
            return FakeResponse({"status_code": "FINISHED"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr("app.publishers.get_platform_secret", lambda _platform: "test-token")
    monkeypatch.setattr("app.publishers.get_platform_value", lambda _platform, _key: "123456")

    from app.publishers import InstagramPublisher

    cover = "https://factory.example/media/job-instagram.jpg"
    result = InstagramPublisher().publish(
        title="Not duplicated",
        description="Caption with #Hindi",
        media_path="https://factory.example/media/job.mp4",
        cover_url=cover,
    )

    assert result.status == "published"
    container_params = calls[0][1]["params"]
    assert container_params["caption"] == "Caption with #Hindi"
    assert container_params["cover_url"] == cover
    get_settings.cache_clear()


def test_openai_media_generation_uses_official_models(tmp_path, monkeypatch):
    import base64
    import httpx
    from app.google_media import _create_openai_assets

    monkeypatch.setattr("app.google_media.get_provider_secret", lambda _provider: "test-openai-key")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "speech" in url:
            class FakeAudioResponse:
                status_code = 200
                content = b"fake-audio"
                def raise_for_status(self): pass
            return FakeAudioResponse()
        if "generations" in url:
            fake_b64 = base64.b64encode(b"fake-image-bytes").decode("ascii")
            return FakeResponse({"data": [{"b64_json": fake_b64}]})
        raise AssertionError(f"Unexpected url: {url}")

    monkeypatch.setattr(httpx, "post", fake_post)

    assets = _create_openai_assets(
        "job-123",
        "psychology topic",
        "namaste script",
        tmp_path,
        generate_image=True,
        generate_voice=True,
    )

    assert "voiceover" in assets
    assert "image" in assets
    assert len(calls) == 2

    speech_call = next(c for c in calls if "speech" in c[0])
    assert speech_call[1]["json"]["model"] == "gpt-4o-mini-tts"

    image_call = next(c for c in calls if "generations" in c[0])
    assert image_call[1]["json"]["model"] == "gpt-image-2.5-flare"
    assert image_call[1]["json"]["size"] == "1024x1792"
    assert image_call[1]["json"]["quality"] == "medium"
    assert "response_format" not in image_call[1]["json"]
