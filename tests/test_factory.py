from pathlib import Path
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_import_is_clean():
    from app.main import app

    assert app.title == "Hindi Content Factory"


def test_publish_job_accepts_short_id():
    from app.db import SessionLocal
    from app.services import _resolve_job, create_job

    job = create_job("Short publishing ID test", "both", request_id="short-publish-test")

    with SessionLocal() as session:
        resolved = _resolve_job(session, job.id[:8])

    assert resolved.id == job.id


def test_factory_starts_paused():
    from app.config import get_settings

    assert get_settings().app_paused is True


def test_manager_read_actions_and_audit(monkeypatch):
    from app.db import ManagerAudit, SessionLocal
    from app.manager import process_manager_message
    from app.manager_brain import TOOLS

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    _mock_manager_model(
        monkeypatch,
        [
            _tool_response("get_factory_status", "{}"),
            _text_response("Factory is healthy and paused."),
        ],
    )

    status = process_manager_message("status", owner_id="12345", channel="telegram")
    assert "healthy and paused" in status

    with SessionLocal() as session:
        audits = session.query(ManagerAudit).filter_by(owner_id="12345").all()
    assert any(audit.channel == "telegram" and audit.decision == "received" for audit in audits)
    assert any(audit.decision == "executed" and "get_factory_status" in audit.request for audit in audits)
    assert any(item["function"]["name"] == "publish_job" for item in TOOLS)


def test_manager_risky_action_requires_persisted_confirmation(monkeypatch):
    from app.db import ManagerConfirmation, SessionLocal
    from app.manager import process_manager_message

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    _mock_manager_model(
        monkeypatch,
        [
            _tool_response("set_factory_paused", '{"paused": true}'),
            _text_response("I need your confirmation to pause the factory."),
        ],
    )

    response = process_manager_message("pause", owner_id="12345", channel="telegram")
    assert "Confirmation required" in response
    assert "/manager confirm " in response

    token = response.rsplit(" ", 1)[1]
    with SessionLocal() as session:
        confirmation = session.get(ManagerConfirmation, token)
    assert confirmation is not None
    assert confirmation.owner_id == "12345"
    assert confirmation.action == "pause"

    wrong_owner = process_manager_message(
        f"confirm {token}",
        owner_id="99999",
        channel="telegram",
    )
    assert "another manager session" in wrong_owner


def test_manager_confirmation_executes_only_for_matching_owner(monkeypatch):
    from app.db import ManagerConfirmation, SessionLocal
    from app.manager import process_manager_message
    from app.services import is_paused

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    _mock_manager_model(
        monkeypatch,
        [
            _tool_response("set_factory_paused", '{"paused": false}'),
            _text_response("I need your confirmation to resume the factory."),
        ],
    )

    request = process_manager_message("resume", owner_id="12345", channel="telegram")
    token = request.rsplit(" ", 1)[1]
    result = process_manager_message(
        f"confirm {token}",
        owner_id="12345",
        channel="telegram",
    )

    assert "Factory resumed" in result
    assert is_paused() is False
    replay = process_manager_message(
        f"confirm {token}",
        owner_id="12345",
        channel="telegram",
    )
    assert "already-used" in replay
    with SessionLocal() as session:
        confirmation = session.get(ManagerConfirmation, token)
    assert confirmation.executed == 1


def _tool_response(name: str, arguments: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Checking that now.",
                    "reasoning_content": "Private reasoning stays inside the model conversation.",
                    "tool_calls": [
                        {
                            "id": "call_test_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ]
    }


def _multi_tool_response(calls: list[tuple[str, str]]) -> dict:
    response = _tool_response(calls[0][0], calls[0][1])
    response["choices"][0]["message"]["tool_calls"] = [
        {
            "id": f"call_test_{index}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
        for index, (name, arguments) in enumerate(calls, start=1)
    ]
    return response


def _text_response(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _mock_manager_model(monkeypatch, responses: list[dict]) -> list[dict]:
    import app.manager_brain as manager_brain

    pending = list(responses)
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, body: dict):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return FakeResponse(pending.pop(0))

    monkeypatch.setattr(manager_brain, "_manager_api_key", lambda: "test-manager-key")
    monkeypatch.setattr(manager_brain.httpx, "Client", FakeClient)
    return calls


def test_manager_brain_preserves_tool_context_and_conversation(monkeypatch):
    from app.db import ManagerMessage, SessionLocal
    from app.manager import process_manager_message

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    calls = _mock_manager_model(
        monkeypatch,
        [
            _tool_response("get_factory_controls", "{}"),
            _text_response("Automation is currently disabled."),
            _text_response("Yes, it is still disabled."),
        ],
    )

    first = process_manager_message(
        "Is automation enabled?",
        owner_id="banti-owner",
        channel="telegram",
    )
    second = process_manager_message(
        "and is it still that way?",
        owner_id="banti-owner",
        channel="telegram",
    )

    assert "disabled" in first
    assert "still disabled" in second
    assert calls[0]["json"]["model"] == "MiniMax-M3.1-Flash"
    assert calls[0]["json"]["reasoning_effort"] == "medium"
    tool_round_messages = calls[1]["json"]["messages"]
    assert any(item.get("role") == "assistant" and item.get("reasoning_content") for item in tool_round_messages)
    assert any(item.get("role") == "tool" for item in calls[1]["json"]["messages"])
    with SessionLocal() as session:
        history = session.query(ManagerMessage).filter_by(owner_id="banti-owner").all()
    assert [item.role for item in history] == ["user", "assistant", "user", "assistant"]


def test_manager_creating_a_job_waits_for_owner_confirmation(monkeypatch):
    from app.db import ContentJob, ManagerConfirmation, SessionLocal
    from app.manager import process_manager_message

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    _mock_manager_model(
        monkeypatch,
        [
            _tool_response(
                "create_job",
                '{"topic": "Why oceans glow at night", "platform": "instagram"}',
            ),
            _text_response("I have prepared the job request."),
        ],
    )

    response = process_manager_message(
        "Make an Instagram reel about glowing oceans.",
        owner_id="12345",
    )

    assert "Confirmation required: create a job for Why oceans glow at night" in response
    with SessionLocal() as session:
        assert session.query(ContentJob).count() == 0
        confirmation = session.query(ManagerConfirmation).first()
    assert confirmation.action == "create"
    assert "Why oceans glow at night" in confirmation.args_json


def test_manager_allows_only_one_mutating_action_per_message(monkeypatch):
    from app.db import ManagerConfirmation, SessionLocal
    from app.manager import process_manager_message

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()
    _mock_manager_model(
        monkeypatch,
        [
            _multi_tool_response(
                [
                    ("set_factory_paused", '{"paused": true}'),
                    ("set_automation_enabled", '{"enabled": true}'),
                ]
            ),
            _text_response("I can only make one change at a time."),
        ],
    )

    response = process_manager_message(
        "Pause the factory and enable automation.",
        owner_id="12345",
    )

    assert "only make one change" in response
    assert "Confirmation required: pause the factory" in response
    with SessionLocal() as session:
        assert session.query(ManagerConfirmation).count() == 1


def test_manager_never_sends_or_persists_pasted_api_key(monkeypatch):
    from app.db import ManagerAudit, ManagerMessage, SessionLocal
    from app.manager import process_manager_message

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    response = process_manager_message(
        "Set API key: abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        owner_id="12345",
        channel="telegram",
    )

    assert "does not accept API keys" in response
    with SessionLocal() as session:
        assert session.query(ManagerMessage).filter_by(owner_id="12345").count() == 0
        audit = session.query(ManagerAudit).filter_by(owner_id="12345").first()
        assert "abcdefghijklmnopqrstuvwxyz" not in audit.request


def test_manager_without_minimax_key_explains_configuration(monkeypatch):
    from app.manager import process_manager_message
    import app.manager_brain as manager_brain

    monkeypatch.setenv("MANAGER_ENABLED", "true")
    monkeypatch.setenv("MANAGER_MINIMAX_API_KEY", "")
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(manager_brain, "_manager_api_key", lambda: "")

    response = process_manager_message("How is the factory doing?", owner_id="12345")

    assert "MiniMax brain is not configured" in response


def test_manager_http_endpoints_require_admin_token():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.post("/api/manager/chat", json={"message": "status"})
    assert response.status_code == 401


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

    with SessionLocal() as session:
        session.get(ContentJob, job.id).stage = "render"
        session.commit()
    claimed = claim_render_job("railway-renderer")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.claimed_by == "railway-renderer"
    assert claim_render_job("railway-renderer") is None


def test_auto_render_target_is_persisted_as_railway_only():
    from app.hq import get_render_worker_target, set_render_worker_target

    assert get_render_worker_target() == "railway-renderer"
    set_render_worker_target("auto")
    assert get_render_worker_target() == "railway-renderer"


def test_custom_series_planner_supports_variable_part_counts():
    from app.services import plan_custom_series

    plan = plan_custom_series("4 भाग की गंभीर साइबर सुरक्षा कहानी", 4)
    assert plan["part_count"] == 4
    assert "serious" in str(plan["tone"]).lower()
    assert "premium" in str(plan["visual_style"]).lower()

    plan = plan_custom_series("मज़ेदार बच्चों की science कहानी", 7)
    assert plan["part_count"] == 7
    assert "witty" in str(plan["tone"]).lower()
    assert "families" in str(plan["audience"]).lower()


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


def test_minimax_provider_is_registered_but_disabled_in_no_paid_mode(monkeypatch):
    from app.db import init_db
    from app.provider_credentials import (
        PROVIDER_INFO,
        PROVIDER_ROLES,
        no_paid_providers_enabled,
        set_no_paid_providers_enabled,
        set_provider_for_role,
    )

    init_db()
    set_no_paid_providers_enabled(True)
    set_provider_for_role("video", "minimax")

    assert PROVIDER_INFO["minimax"]["wired"] is True
    assert PROVIDER_ROLES["video"]["minimax"] == "MiniMax H3 text-to-video"
    assert no_paid_providers_enabled() is True


def test_higgsfield_provider_is_wired_for_video(monkeypatch):
    from app.db import init_db
    from app.provider_credentials import (
        PROVIDER_INFO,
        PROVIDER_ROLES,
        no_paid_providers_enabled,
        set_no_paid_providers_enabled,
        set_provider_for_role,
    )

    init_db()
    set_no_paid_providers_enabled(True)
    set_provider_for_role("video", "higgsfield")

    assert PROVIDER_INFO["higgsfield"]["wired"] is True
    assert PROVIDER_ROLES["video"]["higgsfield"] == "Higgsfield · Wan 2.6 (text only)"
    assert "higgsfield_seedance" in PROVIDER_ROLES["video"]
    assert no_paid_providers_enabled() is True


def test_minimax_video_submission_poll_and_download(monkeypatch, tmp_path):
    import httpx
    from unittest.mock import patch

    from app.google_media import generate_minimax_video
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MINIMAX_API_KEY", "test_minimax_key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.io/v2")
    get_settings.cache_clear()

    submit_response = httpx.Response(
        200,
        json={"task_id": "task-123"},
        request=httpx.Request("POST", "https://api.minimax.io/v2/video_generation"),
    )
    poll_response = httpx.Response(
        200,
        json={
            "task": {
                "status": "succeeded",
                "content": {"url": "https://cdn.example/video.mp4"},
            }
        },
        request=httpx.Request("GET", "https://api.minimax.io/v2/query/video_generation/task-123"),
    )
    download_response = httpx.Response(
        200,
        content=b"mp4-data",
        request=httpx.Request("GET", "https://cdn.example/video.mp4"),
    )
    destination = tmp_path / "scene.mp4"

    with (
        patch("app.google_media.httpx.post", return_value=submit_response) as post,
        patch("app.google_media.httpx.get", side_effect=[poll_response, download_response]) as get,
    ):
        generate_minimax_video("A cinematic safe-driving scene", destination)

    assert destination.read_bytes() == b"mp4-data"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test_minimax_key"
    assert post.call_args.kwargs["json"]["model"] == "MiniMax-H3"
    assert get.call_args_list[0].args[0].endswith("/query/video_generation/task-123")


def test_higgsfield_video_submission_poll_and_download(monkeypatch, tmp_path):
    import httpx
    from unittest.mock import patch

    from app.google_media import generate_higgsfield_video
    from app.config import get_settings

    get_settings.cache_clear()
    destination = tmp_path / "higgsfield.mp4"
    submit_response = httpx.Response(
        200,
        json={
            "status": "queued",
            "request_id": "request-123",
            "status_url": "https://api.higgsfield.ai/requests/request-123/status",
        },
        request=httpx.Request("POST", "https://api.higgsfield.ai/wan/v2.6/text-to-video"),
    )
    poll_response = httpx.Response(
        200,
        json={
            "status": "completed",
            "request_id": "request-123",
            "video": {"url": "https://cdn.example/higgsfield.mp4"},
        },
        request=httpx.Request("GET", "https://api.higgsfield.ai/requests/request-123/status"),
    )
    download_response = httpx.Response(
        200,
        content=b"higgsfield-mp4",
        request=httpx.Request("GET", "https://cdn.example/higgsfield.mp4"),
    )

    with (
        patch("app.google_media.get_provider_secret", return_value="key-id:key-secret"),
        patch("app.google_media.httpx.post", return_value=submit_response) as post,
        patch("app.google_media.httpx.get", side_effect=[poll_response, download_response]) as get,
        patch("app.google_media.time.sleep"),
    ):
        generate_higgsfield_video("A premium Hindi cinematic scene", destination)

    assert destination.read_bytes() == b"higgsfield-mp4"
    assert post.call_args.args[0].endswith("/wan/v2.6/text-to-video")
    assert post.call_args.kwargs["headers"]["Authorization"] == "Key key-id:key-secret"
    assert post.call_args.kwargs["json"]["duration"] == 5
    assert get.call_args_list[0].args[0].endswith("/requests/request-123/status")


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
