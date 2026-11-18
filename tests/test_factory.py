from pathlib import Path
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_import_is_clean():
    from app.main import app

    assert app.title == "Hindi Content Factory"


def test_factory_starts_paused():
    from app.config import get_settings

    assert get_settings().app_paused is True


def test_only_railway_render_worker_can_claim_jobs():
    from app.db import ContentJob, SessionLocal, init_db
    from app.hq import set_render_worker_target
    from app.services import claim_render_job, create_job

    init_db()
    job = create_job("Worker selection test", "youtube")
    with pytest.raises(ValueError, match="Only the Railway render worker"):
        set_render_worker_target("rdp-renderer")

    assert claim_render_job("rdp-renderer") is None
    with SessionLocal() as session:
        assert session.get(ContentJob, job.id).status == "queued"

    claimed = claim_render_job("railway-renderer")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.claimed_by == "railway-renderer"


def test_auto_render_target_is_persisted_as_railway_only():
    from app.hq import get_render_worker_target, set_render_worker_target

    assert get_render_worker_target() == "railway-renderer"

    set_render_worker_target("auto")
    assert get_render_worker_target() == "railway-renderer"


def test_gemini_script_route_works_in_no_paid_mode(monkeypatch):
    import httpx
    from unittest.mock import patch

    from app.scriptwriter import build_script

    generated = "यह एक मौलिक हिंदी स्क्रिप्ट है। " * 8
    response = httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": generated}]}}]},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="gemini"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=True),
        patch("app.provider_credentials.provider_cooldown_until", return_value=None),
        patch("app.provider_credentials.get_provider_secret", return_value="test-key"),
        patch("app.scriptwriter.httpx.post", return_value=response) as request,
    ):
        result = build_script("भारत का इतिहास")

    assert result == generated.strip()
    assert request.call_args.args[0].endswith("gemini-3.5-flash-lite:generateContent")
    assert request.call_args.kwargs["headers"] == {"x-goog-api-key": "test-key"}


def test_huggingface_script_route_uses_inference_providers_api():
    import httpx
    from unittest.mock import patch

    from app.scriptwriter import build_script

    generated = "यह Hugging Face से बना मौलिक हिंदी स्क्रिप्ट है। " * 8
    response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": generated}}]},
        request=httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions"),
    )
    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="huggingface"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=False),
        patch("app.provider_credentials.provider_cooldown_until", return_value=None),
        patch("app.provider_credentials.get_provider_secret", return_value="hf_test_token"),
        patch("app.scriptwriter.httpx.post", return_value=response) as request,
    ):
        result = build_script("भारत का इतिहास")

    assert result == generated.strip()
    assert request.call_args.args[0] == "https://router.huggingface.co/v1/chat/completions"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer hf_test_token"
    assert request.call_args.kwargs["json"]["model"] == "Qwen/Qwen2.5-7B-Instruct"


def test_huggingface_script_route_is_blocked_by_no_paid_mode():
    from unittest.mock import patch

    from app.scriptwriter import build_script

    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="huggingface"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=True),
        patch("app.provider_credentials.get_provider_secret", return_value="hf_test_token"),
        patch("app.scriptwriter.httpx.post") as request,
    ):
        result = build_script("भारत का इतिहास")

    assert "भारत का इतिहास" in result
    request.assert_not_called()


def test_no_paid_mode_blocks_openai_script_route():
    from unittest.mock import patch

    from app.scriptwriter import build_script

    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="openai"),
        patch("app.provider_credentials.no_paid_providers_enabled", return_value=True),
        patch("app.scriptwriter.httpx.post") as request,
    ):
        result = build_script("भारत का इतिहास")

    assert "भारत का इतिहास" in result
    request.assert_not_called()


def test_cybersecurity_reel_template_has_specific_safety_steps():
    from unittest.mock import patch

    from app.scriptwriter import build_script

    with (
        patch("app.provider_credentials.get_provider_for_role", return_value="template"),
        patch("app.scriptwriter._resolve_format_and_niche", return_value=("reel", "cyber safety")),
        patch("app.scriptwriter.httpx.post") as request,
    ):
        script = build_script("साइबर सुरक्षा: फिशिंग लिंक पहचानें और OTP साझा न करें")

    assert "वेबसाइट का पता ध्यान से जाँचें" in script
    assert "OTP नहीं माँगता" in script
    assert "official helpline" in script
    request.assert_not_called()


def test_api_generation_uses_selected_destination_unless_overridden():
    from app.db import ContentJob, SessionLocal, init_db
    from app.hq import set_publish_target
    from app.main import GenerateRequest, generate

    init_db()
    set_publish_target("both")
    generated = generate(GenerateRequest(topic="Selected target"), None)
    with SessionLocal() as session:
        assert session.get(ContentJob, generated["id"]).platform == "both"

    explicit = generate(
        GenerateRequest(topic="Explicit target", platform="youtube"),
        None,
    )
    with SessionLocal() as session:
        assert session.get(ContentJob, explicit["id"]).platform == "youtube"


def test_dual_publish_preserves_success_when_second_platform_fails(tmp_path: Path, monkeypatch):
    import json
    from types import SimpleNamespace

    from app.db import ContentJob, PublicationRecord, SessionLocal, init_db
    from app.publishers import PublicationResult
    from app.services import create_job, publish_job

    init_db()
    media = tmp_path / "render.mp4"
    media.write_bytes(b"v" * 2048)
    for platform in ("youtube", "instagram"):
        (tmp_path / f"{platform}.jpg").write_bytes(b"image")

    job = create_job("Shared topic", "both")
    with SessionLocal() as session:
        stored = session.get(ContentJob, job.id)
        stored.status = "approved"
        stored.approval_version = stored.content_version
        stored.render_path = str(media)
        stored.metadata_json = json.dumps({
            "title": "Shared topic",
            "description": "Description",
            "youtube": {},
            "instagram": {},
            "thumbnails": {
                "youtube": "youtube.jpg",
                "instagram": "instagram.jpg",
            },
        })
        session.commit()

    attempts = {"youtube": 0, "instagram": 0}
    fail_instagram_once = {"value": True}

    class FakePublisher:
        def __init__(self, platform):
            self.platform = platform

        def publish(self, **_kwargs):
            attempts[self.platform] += 1
            if self.platform == "instagram" and fail_instagram_once["value"]:
                fail_instagram_once["value"] = False
                return PublicationResult(status="blocked", detail="temporary failure")
            return PublicationResult(
                status="published",
                remote_id=f"{self.platform}-id",
            )

    monkeypatch.setattr(
        "app.services.get_settings",
        lambda: SimpleNamespace(
            media_root=str(tmp_path),
            public_base_url="https://factory.example",
        ),
    )
    monkeypatch.setattr("app.services.is_paused", lambda: False)
    monkeypatch.setattr("app.services.validate_content", lambda **_kwargs: SimpleNamespace(passed=True, blockers=[]))
    monkeypatch.setattr("app.services.get_publisher", FakePublisher)
    monkeypatch.setattr("app.hq.is_mock_publishing", lambda: False)
    monkeypatch.setattr("app.hq.get_daily_publish_limit", lambda: 6)

    with pytest.raises(ValueError, match="temporary failure"):
        publish_job(job.id)

    with SessionLocal() as session:
        youtube_record = session.query(PublicationRecord).filter_by(
            job_id=job.id,
            platform="youtube",
        ).one()
        assert youtube_record.status == "published"

    assert publish_job(job.id).status == "published"
    assert attempts == {"youtube": 1, "instagram": 2}


def test_worker_thumbnail_upload_registers_cover_after_approval(monkeypatch, tmp_path: Path):
    from app import main

    job = SimpleNamespace(
        id="job-1",
        status="rendering",
        metadata_json='{"title":"Test"}',
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, job_id):
            return job if job_id == job.id else None

        def scalar(self, _query):
            return job

        def commit(self):
            pass

    monkeypatch.setattr(main, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(media_root=tmp_path, max_storage_gb=1),
    )
    image = b"\xff\xd8\xff" + b"x" * 2048
    upload = UploadFile(
        file=BytesIO(image),
        filename="cover.jpg",
        headers=Headers({"content-type": "image/jpeg"}),
    )

    result = main.worker_upload_thumbnail(job.id, "youtube", upload, None)

    filename = result["filename"]
    assert filename == "job-1-youtube.jpg"
    assert (tmp_path / filename).read_bytes() == image
    assert '"youtube": "job-1-youtube.jpg"' in job.metadata_json

    with pytest.raises(HTTPException) as error:
        main.public_media(filename)
    assert error.value.status_code == 404

    job.status = "approved"
    response = main.public_media(filename)
    assert Path(response.path) == tmp_path / filename
    assert response.media_type == "image/jpeg"


@pytest.mark.asyncio
async def test_youtube_oauth_callback_gives_clear_next_steps(monkeypatch):
    from unittest.mock import AsyncMock

    from app import main

    exchange = AsyncMock()
    monkeypatch.setattr(main, "exchange_code", exchange)
    response = await main.youtube_callback(code="oauth-code", state="oauth-state")

    exchange.assert_awaited_once_with("oauth-code", "oauth-state")
    assert response.status_code == 200
    assert "YouTube connected" in response.body.decode()
    assert "Verify YouTube channel" in response.body.decode()
