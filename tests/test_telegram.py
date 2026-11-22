"""Tests for the Telegram bot UI layer.

These tests validate message formatting, keyboard layout, error sanitisation,
and the handle_update dispatcher without calling the real Telegram API.
They stay within Antigravity's handoff boundary: Telegram wording, button
layout, command UX, and Telegram-only tests.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ── Shared fixtures ─────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "12345")
    from app.config import get_settings
    from app.telegram import (
        _pending_elevenlabs_voice_id,
        _pending_fish_audio_reference_id,
        _pending_huggingface_image_endpoint,
        _pending_minimax_voice_id,
        _pending_instagram_id,
        _pending_account_name,
        _pending_audio_upload,
        _pending_clip_source,
        _pending_icon_slot,
        _pending_custom_creator,
        _pending_custom_part_count,
        _unauthorized_notice_times,
    )

    get_settings.cache_clear()
    _unauthorized_notice_times.clear()
    _pending_instagram_id.clear()
    _pending_account_name.clear()
    _pending_audio_upload.clear()
    _pending_clip_source.clear()
    _pending_icon_slot.clear()
    _pending_custom_creator.clear()
    _pending_custom_part_count.clear()
    _pending_elevenlabs_voice_id.clear()
    _pending_fish_audio_reference_id.clear()
    _pending_minimax_voice_id.clear()
    _pending_huggingface_image_endpoint.clear()
    import app.db
    from app.db import Base, engine, init_db

    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
    get_settings.cache_clear()


OWNER_ID = 12345
CHAT_ID = 12345


def _text_update(text: str, update_id: int | None = None) -> dict:
    """Build a minimal Telegram text message update."""
    update = {
        "message": {
            "text": text,
            "from": {"id": OWNER_ID},
            "chat": {"id": CHAT_ID, "type": "private"},
        }
    }
    if update_id is not None:
        update["update_id"] = update_id
    return update


def _callback_update(data: str) -> dict:
    """Build a minimal Telegram callback_query update."""
    return {
        "callback_query": {
            "id": "cb_123",
            "data": data,
            "from": {"id": OWNER_ID},
            "message": {
                "chat": {"id": CHAT_ID, "type": "private"},
            },
        }
    }


# ── Formatting helper tests ────────────────────────────────────────────
class TestFormatting:
    def test_score_bar_zero(self):
        from app.telegram import _score_bar

        result = _score_bar(0, 10)
        assert "░░░░░░░░░░" in result
        assert "0/100" in result

    def test_score_bar_full(self):
        from app.telegram import _score_bar

        result = _score_bar(100, 10)
        assert "██████████" in result
        assert "100/100" in result

    def test_score_bar_half(self):
        from app.telegram import _score_bar

        result = _score_bar(50, 10)
        assert "█████░░░░░" in result

    def test_loading_bar_variations(self):
        from app.telegram import _loading_bar

        assert _loading_bar(100, 10) == "[██████████] 100%"
        assert _loading_bar(0, 10) == "[░░░░░░░░░░] 0%"
        assert "█████░░░░░" in _loading_bar(50, 10)
        assert _loading_bar(150, 10) == "[██████████] 100%"
        assert _loading_bar(-20, 10) == "[░░░░░░░░░░] 0%"

    def test_pipeline_bar_stages(self):
        from app.telegram import _pipeline_bar

        assert "Queued" in _pipeline_bar("queued")
        assert "Rendering" in _pipeline_bar("rendering")
        assert "Ready for review" in _pipeline_bar("awaiting_approval")
        assert "Approved" in _pipeline_bar("approved")
        assert "100%" in _pipeline_bar("published")
        assert "Failed" in _pipeline_bar("failed")

    def test_divider_is_consistent(self):
        from app.telegram import _divider

        assert len(_divider()) > 10
        assert _divider() == _divider()

    def test_format_job_card_compact(self):
        from app.telegram import _format_job_card

        job = MagicMock()
        job.id = "abcdef12-3456-7890-abcd-ef1234567890"
        job.topic = "Test Topic"
        job.status = "queued"
        job.stage = "topic"
        job.brand = "test_brand"
        job.platform = "both"
        job.content_version = 1
        job.approval_version = None
        job.error_message = None

        result = _format_job_card(job, compact=True)
        assert "abcdef12" in result
        assert "Test Topic" in result
        assert "🟡" in result  # queued emoji

    def test_format_job_card_full(self):
        from app.telegram import _format_job_card

        job = MagicMock()
        job.id = "abcdef12-3456-7890-abcd-ef1234567890"
        job.topic = "Hindi AI Explainer"
        job.status = "approved"
        job.stage = "quality_check"
        job.brand = "learn_with_elite_explainer"
        job.platform = "youtube"
        job.content_version = 2
        job.approval_version = 2
        job.error_message = None

        result = _format_job_card(job, compact=False)
        assert "Hindi AI Explainer" in result
        assert "🟢" in result  # approved emoji
        assert "Quality review" in result
        assert "v2" in result


class TestSafeError:
    def test_known_error_passed_through(self):
        from app.telegram import _safe_error_text

        exc = ValueError("Job not found")
        assert _safe_error_text(exc) == "Job not found"

    def test_factory_paused_passed_through(self):
        from app.telegram import _safe_error_text

        exc = ValueError("Factory is paused")
        assert _safe_error_text(exc) == "Factory is paused"

    def test_unknown_error_is_hidden(self):
        from app.telegram import _safe_error_text

        exc = RuntimeError("psycopg2.OperationalError: connection refused")
        result = _safe_error_text(exc)
        assert "psycopg2" not in result
        assert "internal error" in result.lower()

    def test_xss_in_error_is_escaped(self):
        from app.telegram import _safe_error_text

        exc = ValueError("Job not found <script>alert(1)</script>")
        result = _safe_error_text(exc)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestTelegramPolling:
    def test_polling_mode_is_configurable_for_private_rdp(self, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("TELEGRAM_MODE", "polling")
        get_settings.cache_clear()
        assert get_settings().telegram_mode == "polling"
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_poll_once_dispatches_updates_and_persists_next_offset(self, monkeypatch):
        from app import telegram

        received = []
        saved = []

        async def handle(update):
            received.append(update["update_id"])

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "ok": True,
                    "result": [{"update_id": 41}, {"update_id": 42}],
                }

        class FakeClient:
            async def get(self, url, params):
                assert url.endswith("/getUpdates")
                assert params["offset"] == 40
                assert params["timeout"] == 50
                return FakeResponse()

        monkeypatch.setattr(telegram, "handle_update", handle)
        monkeypatch.setattr(telegram, "_save_telegram_polling_offset", saved.append)
        next_offset = await telegram._poll_telegram_once(FakeClient(), "test-token", 40)

        assert received == [41, 42]
        assert saved == [42, 43]
        assert next_offset == 43

    @pytest.mark.asyncio
    async def test_poll_once_rejects_malformed_response_without_advancing(self, monkeypatch):
        from app import telegram

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": [{"update_id": "not-an-integer"}]}

        class FakeClient:
            async def get(self, url, params):
                return FakeResponse()

        saved = []
        monkeypatch.setattr(telegram, "_save_telegram_polling_offset", saved.append)
        with pytest.raises(RuntimeError, match="invalid update"):
            await telegram._poll_telegram_once(FakeClient(), "test-token", None)
        assert saved == []

    @pytest.mark.asyncio
    async def test_webhook_registration_retries_transient_network_failure(self, monkeypatch):
        from types import SimpleNamespace
        import httpx
        from app import telegram

        calls = []
        requests = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url, json=None, **_kwargs):
                requests.append((url.rsplit("/", 1)[-1], json))
                calls.append(url.rsplit("/", 1)[-1])
                if url.endswith("/setWebhook") and calls.count("setWebhook") == 1:
                    raise httpx.ConnectError("temporary network failure")
                return FakeResponse()

        monkeypatch.setattr(
            telegram,
            "get_settings",
            lambda: SimpleNamespace(
                telegram_bot_token="test-token",
                public_base_url="https://factory.example",
                telegram_webhook_secret="valid_secret-123",
                owner_ids={12345},
            ),
        )
        monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
        monkeypatch.setattr(telegram.asyncio, "sleep", AsyncMock())

        await telegram.configure_webhook()

        assert calls.count("setWebhook") == 2
        assert calls.count("deleteMyCommands") == 1
        assert calls.count("setMyCommands") == 1
        assert requests[-2] == (
            "deleteMyCommands",
            {"scope": {"type": "default"}},
        )
        assert requests[-1][1]["scope"] == {"type": "chat", "chat_id": OWNER_ID}
        assert any(
            command["command"] == "preview"
            for command in requests[-1][1]["commands"]
        )


# ── Keyboard layout tests ──────────────────────────────────────────────
class TestKeyboards:
    def test_main_keyboard_has_rows(self):
        from app.telegram import _keyboard

        kb = _keyboard()
        rows = kb["inline_keyboard"]
        assert len(rows) == 4

    def test_custom_part_count_keyboard_includes_4_and_7(self):
        from app.telegram import _custom_part_count_keyboard

        keyboard = _custom_part_count_keyboard()
        buttons = [
            button
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        callback_data = {button["callback_data"] for button in buttons if "callback_data" in button}
        assert "custom_parts:4" in callback_data
        assert "custom_parts:7" in callback_data

    def test_main_keyboard_shows_resume_when_paused(self):
        """When factory is paused, the toggle button should say Resume."""
        from app.telegram import _keyboard

        kb = _keyboard()
        # Factory starts paused in tests
        last_row = kb["inline_keyboard"][3]
        texts = [btn["text"] for btn in last_row]
        assert any("Resume" in t for t in texts)

    def test_main_keyboard_shows_pause_when_running(self):
        from app.services import set_paused
        from app.telegram import _keyboard

        set_paused(False)
        kb = _keyboard()
        last_row = kb["inline_keyboard"][3]
        texts = [btn["text"] for btn in last_row]
        assert any("Pause" in t for t in texts)

    def test_controls_exposes_audio_library(self):
        from app.telegram import _controls_keyboard

        callbacks = [
            button["callback_data"]
            for row in _controls_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "audio_catalog" in callbacks

    def test_studio_exposes_clipping_engine(self):
        from app.telegram import _studio_keyboard

        callbacks = [
            button["callback_data"]
            for row in _studio_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "clip_studio" in callbacks

    def test_job_keyboard_has_actions(self):
        from app.telegram import _job_keyboard

        kb = _job_keyboard("test-job-id-1234")
        rows = kb["inline_keyboard"]
        all_texts = [btn["text"] for row in rows for btn in row]
        assert any("Approve" in t for t in all_texts)
        assert any("Publish" in t for t in all_texts)
        assert any("Quality" in t for t in all_texts)
        assert any("Preview video" in t for t in all_texts)
        assert any("Back" in t for t in all_texts)

    def test_back_keyboard(self):
        from app.telegram import _back_keyboard

        kb = _back_keyboard()
        assert len(kb["inline_keyboard"]) == 1
        assert kb["inline_keyboard"][0][0]["callback_data"] == "menu"

    @pytest.mark.asyncio
    async def test_preview_callback_sends_rendered_video_for_owner(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.db import ContentJob, SessionLocal
        from app.services import create_job
        from app.telegram import handle_update
        import app.telegram as telegram

        job = create_job("Cybersecurity awareness", "instagram")
        video_path = tmp_path / f"{job.id}.mp4"
        video_path.write_bytes(b"mp4-data")
        with SessionLocal() as session:
            stored = session.get(ContentJob, job.id)
            stored.status = "awaiting_approval"
            stored.render_path = str(video_path)
            session.commit()

        monkeypatch.setattr(
            telegram,
            "get_settings",
            lambda: SimpleNamespace(
                owner_ids=[OWNER_ID],
                telegram_bot_token="test-token",
                media_root=tmp_path,
            ),
        )
        with patch("app.telegram.answer_callback", new_callable=AsyncMock), patch(
            "app.telegram.send_video", new_callable=AsyncMock
        ) as send_video:
            await handle_update(_callback_update(f"preview:{job.id}"))

        send_video.assert_awaited_once_with(
            CHAT_ID,
            video_path,
            "Review before approval · Cybersecurity awareness",
        )

    @pytest.mark.asyncio
    async def test_preview_command_accepts_short_job_id(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.db import ContentJob, SessionLocal
        from app.services import create_job
        from app.telegram import handle_update
        import app.telegram as telegram

        job = create_job("Cybersecurity awareness", "instagram")
        video_path = tmp_path / f"{job.id}.mp4"
        video_path.write_bytes(b"mp4-data")
        with SessionLocal() as session:
            stored = session.get(ContentJob, job.id)
            stored.status = "awaiting_approval"
            stored.render_path = str(video_path)
            session.commit()

        monkeypatch.setattr(
            telegram,
            "get_settings",
            lambda: SimpleNamespace(
                owner_ids=[OWNER_ID],
                telegram_bot_token="test-token",
                media_root=tmp_path,
            ),
        )
        with patch("app.telegram.send_video", new_callable=AsyncMock) as send_video:
            await handle_update(_text_update(f"/preview {job.id[:8]}"))

        send_video.assert_awaited_once_with(
            CHAT_ID,
            video_path,
            "Review before approval · Cybersecurity awareness",
        )

    @pytest.mark.asyncio
    async def test_preview_callback_rejects_video_outside_media_root(
        self, tmp_path, monkeypatch
    ):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.db import ContentJob, SessionLocal
        from app.services import create_job
        from app.telegram import handle_update
        import app.telegram as telegram

        job = create_job("Cybersecurity awareness", "instagram")
        outside_video = tmp_path.parent / f"{job.id}.mp4"
        outside_video.write_bytes(b"mp4-data")
        with SessionLocal() as session:
            stored = session.get(ContentJob, job.id)
            stored.status = "awaiting_approval"
            stored.render_path = str(outside_video)
            session.commit()

        monkeypatch.setattr(
            telegram,
            "get_settings",
            lambda: SimpleNamespace(
                owner_ids=[OWNER_ID],
                telegram_bot_token="test-token",
                media_root=tmp_path,
            ),
        )
        with patch("app.telegram.answer_callback", new_callable=AsyncMock), patch(
            "app.telegram.send_video", new_callable=AsyncMock
        ) as send_video, patch("app.telegram.send_message", new_callable=AsyncMock) as send_message:
            await handle_update(_callback_update(f"preview:{job.id}"))

        send_video.assert_not_awaited()
        assert "missing or unavailable" in send_message.await_args.args[1]
        outside_video.unlink()


# ── Message builder tests ──────────────────────────────────────────────
class TestMessageBuilders:
    def test_start_message_content(self):
        from app.telegram import _build_start_message

        msg = _build_start_message()
        assert "Content Factory" in msg
        assert "Approval" in msg

    def test_status_message_shows_paused(self):
        from app.services import set_paused
        from app.telegram import _build_status_message

        set_paused(True)
        msg = _build_status_message()
        assert "PAUSED" in msg
        assert "Mock mode" in msg
        assert "Render worker" in msg
        assert "YouTube" in msg
        assert "Instagram" in msg

    def test_status_message_shows_running(self):
        from app.services import set_paused
        from app.telegram import _build_status_message

        set_paused(False)
        msg = _build_status_message()
        assert "RUNNING" in msg

    def test_empty_queue_message(self):
        from app.telegram import _build_queue_message

        msg = _build_queue_message()
        assert "empty" in msg.lower()

    def test_queue_with_jobs(self):
        from app.services import create_job
        from app.telegram import _build_queue_message

        create_job("Test topic for queue", "youtube")
        msg = _build_queue_message()
        assert "Test topic for queue" in msg
        assert "1 job" in msg

    def test_empty_trends_message(self):
        from app.telegram import _build_trends_message

        msg = _build_trends_message()
        assert "No trend candidates" in msg

    def test_empty_analytics_message(self):
        from app.telegram import _build_analytics_message

        msg = _build_analytics_message()
        assert "No analytics snapshots" in msg

    def test_help_message_has_all_commands(self):
        from app.telegram import _build_help_message

        msg = _build_help_message()
        for cmd in ["/generate", "/preview", "/approve", "/publish", "/status", "/queue", "/trends", "/analytics", "/pause", "/resume", "/menu"]:
            assert cmd in msg, f"Missing {cmd} in help message"

    def test_controls_message(self):
        from app.telegram import _build_controls_message

        msg = _build_controls_message()
        assert "/generate" in msg
        assert "/approve" in msg

    def test_platform_guide_is_short_and_covers_both_platforms(self):
        from app.telegram import _build_platform_guide_message

        message = _build_platform_guide_message()
        assert "setup wizard" in message
        assert "YouTube" in message
        assert "callback URL" in message
        assert "approval" in message.lower()

    def test_platform_guide_has_one_tap_setup_buttons(self):
        from app.telegram import _platform_guide_keyboard

        buttons = [
            button
            for row in _platform_guide_keyboard()["inline_keyboard"]
            for button in row
        ]
        callbacks = [button.get("callback_data") for button in buttons]
        assert "youtube_setup" in callbacks
        assert "instagram_setup_guide" in callbacks

    def test_account_manager_masks_account_ids_and_offers_management_actions(self):
        from app.platform_credentials import set_platform_value
        from app.telegram import _account_manager_keyboard, _build_account_manager_message

        set_platform_value("instagram", "user_id", "17841400000000000")
        message = _build_account_manager_message()
        assert "One connected account per platform" in message
        assert "••••0000" in message
        assert "17841400000000000" not in message

        with patch(
            "app.telegram.connection_status",
            return_value={"configured": True, "authorized": True},
        ):
            callbacks = [
                button["callback_data"]
                for row in _account_manager_keyboard()["inline_keyboard"]
                for button in row
                if "callback_data" in button
            ]
        assert "youtube_setup" in callbacks
        assert "youtube_reconnect" in callbacks
        assert "instagram_id" in callbacks
        assert "instagram_token" in callbacks
        assert "instagram_disconnect_confirm" in callbacks

    def test_removing_instagram_account_clears_saved_values_and_disables_publishing(self):
        from app.db import FactorySetting, ProviderCredential, SessionLocal
        from app.platform_credentials import (
            get_platform_value,
            platform_enabled,
            remove_platform_account,
            set_platform_enabled,
            set_platform_value,
        )

        with SessionLocal() as session:
            session.add(ProviderCredential(provider="platform:instagram", secret_encrypted="encrypted"))
            session.commit()
        set_platform_value("instagram", "user_id", "17841400000000000")
        set_platform_enabled("instagram", True)

        assert remove_platform_account("instagram") is True
        assert get_platform_value("instagram", "user_id") is None
        assert platform_enabled("instagram", True) is False
        with SessionLocal() as session:
            assert session.get(ProviderCredential, "platform:instagram") is None
            assert session.get(FactorySetting, "platform:instagram:enabled").value == "false"

    def test_instagram_verification_returns_the_connected_username(self):
        from app.platform_credentials import set_platform_value, verify_instagram_account

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"id": "17841400000000000", "username": "my_channel"}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def get(self, url, *, headers, params):
                assert url.endswith("/17841400000000000")
                assert headers == {"Authorization": "Bearer test-access-token"}
                assert params == {"fields": "id,username"}
                return FakeResponse()

        set_platform_value("instagram", "user_id", "17841400000000000")
        with (
            patch("app.platform_credentials.get_platform_secret", return_value="test-access-token"),
            patch("app.platform_credentials.httpx.Client", return_value=FakeClient()),
        ):
            assert verify_instagram_account() == {
                "id": "17841400000000000",
                "username": "my_channel",
            }

    @pytest.mark.asyncio
    async def test_short_lived_instagram_token_is_exchanged_and_stored_encrypted(self, monkeypatch):
        from cryptography.fernet import Fernet

        from app.config import get_settings
        from app.platform_credentials import exchange_instagram_token, get_platform_secret

        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setenv("META_APP_ID", "meta-app-id")
        monkeypatch.setenv("META_APP_SECRET", "meta-app-secret")
        get_settings.cache_clear()

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "long-lived-token", "expires_in": 5184000}

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def get(self, url, *, params):
                assert url.endswith("/oauth/access_token")
                assert params["grant_type"] == "fb_exchange_token"
                assert params["client_secret"] == "meta-app-secret"
                assert params["fb_exchange_token"] == "short-lived-token"
                return FakeResponse()

        try:
            with patch("app.platform_credentials.httpx.AsyncClient", FakeClient):
                assert await exchange_instagram_token(" short-lived-token ") == 5184000
            assert get_platform_secret("instagram") == "long-lived-token"
        finally:
            get_settings.cache_clear()

    def test_controls_keyboard_has_platform_guide(self):
        from app.telegram import _controls_keyboard

        callbacks = [
            button["callback_data"]
            for row in _controls_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "platform_guide" in callbacks
        assert "hq:publish_target" in callbacks

    def test_publish_target_is_persisted_and_offers_three_destinations(self):
        from app.hq import get_publish_target, set_publish_target
        from app.telegram import _build_publish_target_message, _publish_target_keyboard

        assert set_publish_target("both") == "both"
        assert get_publish_target() == "both"
        assert "same rendered video" in _build_publish_target_message()
        callbacks = [
            button["callback_data"]
            for row in _publish_target_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "publish_target:instagram" in callbacks
        assert "publish_target:youtube" in callbacks
        assert "publish_target:both" in callbacks

    def test_platform_guide_has_actionable_ordered_setup_steps(self):
        from app.telegram import _build_platform_guide_message, _platform_guide_keyboard

        message = _build_platform_guide_message()
        keyboard = _platform_guide_keyboard()["inline_keyboard"]
        assert "mock mode" in message
        assert any(row[0]["callback_data"] == "youtube_setup" for row in keyboard)
        assert any(row[0]["callback_data"] == "instagram_setup_guide" for row in keyboard)

    def test_youtube_setup_guide_has_cloud_and_railway_steps(self):
        from app.telegram import _build_youtube_setup_message, _youtube_setup_keyboard

        message = "\n".join(_build_youtube_setup_message(step) for step in range(1, 6))
        for expected in (
            "Google Cloud Console",
            "YouTube Data API v3",
            "Web application",
            "Authorized redirect URIs",
            "YOUTUBE_CLIENT_ID",
            "YOUTUBE_CLIENT_SECRET",
            "Connect YouTube",
        ):
            assert expected in message
        assert "/oauth/youtube/callback" in _build_youtube_setup_message(4)

        step_one_buttons = [
            button
            for row in _youtube_setup_keyboard(1)["inline_keyboard"]
            for button in row
        ]
        assert any(button.get("callback_data") == "youtube_step:2" for button in step_one_buttons)

    def test_youtube_callback_url_uses_configured_public_domain(self, monkeypatch):
        from app.config import get_settings
        from app.telegram import _build_youtube_setup_message

        monkeypatch.setenv("PUBLIC_BASE_URL", "https://factory.example")
        get_settings.cache_clear()
        try:
            assert "https://factory.example/oauth/youtube/callback" in _build_youtube_setup_message(4)
        finally:
            get_settings.cache_clear()

    def test_instagram_setup_guide_has_actionable_meta_and_railway_steps(self):
        from app.telegram import _build_instagram_setup_message, _instagram_setup_step_keyboard

        message = "\n".join(_build_instagram_setup_message(step) for step in range(1, 7))
        for expected in (
            "Professional",
            "Meta for Developers",
            "instagram_content_publish",
            "Add Instagram account ID",
            "long-lived",
            "PUBLIC_BASE_URL",
            "approve every post yourself",
        ):
            assert expected in message
        step_five_buttons = [
            button
            for row in _instagram_setup_step_keyboard(5)["inline_keyboard"]
            for button in row
        ]
        assert any(button.get("callback_data") == "instagram_id" for button in step_five_buttons)
        assert any(button.get("callback_data") == "instagram_token" for button in step_five_buttons)

    def test_provider_settings_explain_each_generation_role(self):
        from app.telegram import _provider_keyboard, _provider_message, _provider_role_keyboard

        message = _provider_message()
        assert "Script" in message
        assert "Image" in message
        assert "Voice" in message
        assert "FFmpeg" in message

        callbacks = [
            button["callback_data"]
            for row in _provider_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "provider_role:script" in callbacks
        assert "provider_role:image" in callbacks
        assert "provider_role:voice" in callbacks
        assert "provider_role:video" in callbacks
        assert "video_pipeline" in callbacks

    def test_video_renderer_offers_only_railway_worker(self, monkeypatch):
        from app.telegram import _provider_video_keyboard, _provider_video_message

        monkeypatch.setattr(
            "app.telegram.worker_status",
            lambda: [
                {"worker_id": "rdp-renderer", "online": True},
                {"worker_id": "railway-renderer", "online": False},
            ],
        )
        buttons = [
            button
            for row in _provider_video_keyboard()["inline_keyboard"]
            for button in row
        ]
        callbacks = [button["callback_data"] for button in buttons]

        assert callbacks[0] == "render_worker:auto"
        assert callbacks[-1] == "providers"
        assert len(callbacks) == 2
        assert "RDP workers are not used" in _provider_video_message()
        assert "Selected worker: <b>railway-renderer</b>" in _provider_video_message()
        assert "Railway worker status: <b>offline</b>" in _provider_video_message()

    @pytest.mark.asyncio
    async def test_video_renderer_rejects_rdp_worker_selection(self, monkeypatch):
        import hashlib
        from app.telegram import handle_update
        from app.hq import get_render_worker_target

        monkeypatch.setattr(
            "app.telegram.worker_status",
            lambda: [{"worker_id": "rdp-renderer", "online": True}],
        )
        token = hashlib.sha256(b"rdp-renderer").hexdigest()[:16]
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update(f"render_worker:{token}"))

        assert get_render_worker_target() == "railway-renderer"
        assert "Only the Railway render worker is enabled" in mock_send.call_args.args[1]

    def test_provider_role_choices_are_saved_independently(self):
        from app.provider_credentials import (
            get_provider_for_role,
            set_provider_for_role,
        )

        set_provider_for_role("script", "openai")
        set_provider_for_role("image", "google")
        set_provider_for_role("voice", "off")

        assert get_provider_for_role("script") == "openai"
        assert get_provider_for_role("image") == "google"
        assert get_provider_for_role("voice") == "off"

    def test_no_paid_mode_blocks_paid_media_and_allows_edge_tts(self):
        from app.provider_credentials import (
            no_paid_providers_enabled,
            provider_candidates_for_role,
            set_elevenlabs_voice_id,
            set_no_paid_providers_enabled,
            set_provider_for_role,
        )

        assert no_paid_providers_enabled()
        set_provider_for_role("voice", "google")
        assert provider_candidates_for_role("voice") == []
        assert provider_candidates_for_role("image") == ["off"]

        set_provider_for_role("voice", "edge_tts")
        assert provider_candidates_for_role("voice") == ["edge_tts"]
        set_provider_for_role("voice", "elevenlabs")
        set_elevenlabs_voice_id("voice_123")
        assert provider_candidates_for_role("voice") == []
        set_no_paid_providers_enabled(False)
        assert not no_paid_providers_enabled()

    def test_quota_errors_cool_down_provider_and_allow_configured_backup(self, monkeypatch):
        import httpx

        from app.provider_credentials import (
            is_provider_quota_error,
            mark_provider_quota_error,
            provider_candidates_for_role,
            provider_cooldown_until,
            set_no_paid_providers_enabled,
        )

        request = httpx.Request("POST", "https://provider.example/generate")
        exhausted_response = httpx.Response(
            403,
            json={"error": {"status": "RESOURCE_EXHAUSTED"}},
            request=request,
        )
        exhausted = httpx.HTTPStatusError(
            "quota exhausted",
            request=request,
            response=exhausted_response,
        )
        permission_response = httpx.Response(
            403,
            json={"error": {"status": "PERMISSION_DENIED"}},
            request=request,
        )
        permission = httpx.HTTPStatusError(
            "permission denied",
            request=request,
            response=permission_response,
        )
        assert is_provider_quota_error(exhausted)
        assert not is_provider_quota_error(permission)

        set_no_paid_providers_enabled(False)
        until = mark_provider_quota_error("google", exhausted)
        assert provider_cooldown_until("google") == until
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        from app.config import get_settings
        get_settings.cache_clear()
        try:
            with (
                patch("app.provider_credentials.get_provider_for_role", return_value="google"),
                patch(
                    "app.provider_credentials.provider_status",
                    return_value={"google": True, "openai": True},
                ),
            ):
                assert provider_candidates_for_role("image") == ["openai"]
        finally:
            get_settings.cache_clear()

    def test_asset_generation_routes_image_and_voice_separately(self, tmp_path):
        from app.google_media import create_job_assets

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch(
                "app.provider_credentials.provider_candidates_for_role",
                side_effect=lambda role: {"image": ["google"], "voice": ["openai"]}[role],
            ),
            patch("app.google_media.generate_image") as mock_image,
            patch(
                "app.google_media._create_openai_assets",
                return_value={"voiceover": "job-voiceover.mp3"},
            ) as mock_openai,
        ):
            assets = create_job_assets("job", "topic", "script", tmp_path)

        assert assets == {
            f"scene_{index}": f"job-scene-{index}.png" for index in range(1, 7)
        } | {"voiceover": "job-voiceover.mp3"}
        assert mock_image.call_count == 6
        mock_openai.assert_called_once_with(
            "job",
            "topic",
            "script",
            tmp_path,
            generate_image=False,
            generate_voice=True,
            image_prompt=None,
        )

    def test_huggingface_image_endpoint_rejects_non_endpoint_urls(self):
        from app.provider_credentials import set_huggingface_image_endpoint

        assert (
            set_huggingface_image_endpoint("https://my-endpoint.endpoints.huggingface.cloud/")
            == "https://my-endpoint.endpoints.huggingface.cloud"
        )
        for value in (
            "http://my-endpoint.endpoints.huggingface.cloud",
            "https://example.com",
            "https://my-endpoint.endpoints.huggingface.cloud/v1",
            "https://user:pass@my-endpoint.endpoints.huggingface.cloud",
            "https://my-endpoint.endpoints.huggingface.cloud?token=secret",
        ):
            with pytest.raises(ValueError):
                set_huggingface_image_endpoint(value)

    def test_huggingface_route_creates_six_storyboard_assets(self, tmp_path):
        from app.google_media import create_job_assets

        storyboard = [
            {"stage": f"stage {index}", "visual_direction": f"visual {index}"}
            for index in range(1, 7)
        ]

        def fake_generate(prompt, destination):
            destination.write_bytes(b"image")

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch("app.provider_credentials.get_provider_for_role", return_value="huggingface"),
            patch("app.provider_credentials.no_paid_providers_enabled", return_value=False),
            patch("app.provider_credentials.provider_status", return_value={"huggingface": True}),
            patch(
                "app.provider_credentials.get_huggingface_image_endpoint",
                return_value="https://test.endpoints.huggingface.cloud",
            ),
            patch("app.provider_credentials.provider_candidates_for_role", return_value=["off"]),
            patch("app.google_media.generate_huggingface_image", side_effect=fake_generate) as generate,
        ):
            assets = create_job_assets(
                "job",
                "topic",
                "\n\n".join(f"script beat {index}" for index in range(1, 7)),
                tmp_path,
                storyboard=storyboard,
            )

        assert assets == {
            f"scene_{index}": f"job-scene-{index}.png"
            for index in range(1, 7)
        }
        assert generate.call_count == 6
        assert all(f"visual {index}" in call.args[0] for index, call in enumerate(generate.call_args_list, 1))

    def test_huggingface_image_request_authenticates_and_saves_png(self, tmp_path):
        import httpx

        from app.google_media import generate_huggingface_image

        request = httpx.Request("POST", "https://test.endpoints.huggingface.cloud")
        response = httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG image bytes",
            request=request,
        )
        destination = tmp_path / "scene.png"
        with (
            patch("app.google_media.get_provider_secret", return_value="hf-test-token"),
            patch(
                "app.provider_credentials.get_huggingface_image_endpoint",
                return_value="https://test.endpoints.huggingface.cloud",
            ),
            patch("app.google_media.httpx.post", return_value=response) as post,
        ):
            generate_huggingface_image("scene prompt", destination)

        assert destination.read_bytes() == b"\x89PNG image bytes"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer hf-test-token"
        assert post.call_args.kwargs["json"]["inputs"] == "scene prompt"
        assert post.call_args.kwargs["json"]["parameters"]["num_inference_steps"] == 4

    def test_huggingface_image_request_retries_endpoint_cold_start(self, tmp_path):
        import httpx

        from app.google_media import generate_huggingface_image

        request = httpx.Request("POST", "https://test.endpoints.huggingface.cloud")
        cold_start = httpx.Response(502, request=request)
        ready = httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG image bytes",
            request=request,
        )
        with (
            patch("app.google_media.get_provider_secret", return_value="hf-test-token"),
            patch(
                "app.provider_credentials.get_huggingface_image_endpoint",
                return_value="https://test.endpoints.huggingface.cloud",
            ),
            patch("app.google_media.httpx.post", side_effect=[cold_start, ready]) as post,
            patch("app.google_media.time.sleep"),
        ):
            generate_huggingface_image("scene prompt", tmp_path / "scene.png")

        assert post.call_count == 2

    def test_huggingface_quota_error_cools_provider_and_cleans_partial_images(self, tmp_path):
        import httpx

        from app.google_media import create_job_assets

        request = httpx.Request("POST", "https://test.endpoints.huggingface.cloud")
        response = httpx.Response(429, request=request)
        quota_error = httpx.HTTPStatusError("rate limit", request=request, response=response)

        def generate_scene(_prompt, path):
            if path.name.endswith("scene-1.png"):
                path.write_bytes(b"partial image")
                return
            raise quota_error

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch("app.provider_credentials.get_provider_for_role", return_value="huggingface"),
            patch("app.provider_credentials.no_paid_providers_enabled", return_value=False),
            patch("app.provider_credentials.provider_status", return_value={"huggingface": True}),
            patch(
                "app.provider_credentials.get_huggingface_image_endpoint",
                return_value="https://test.endpoints.huggingface.cloud",
            ),
            patch("app.provider_credentials.is_provider_quota_error", return_value=True),
            patch("app.provider_credentials.mark_provider_quota_error") as mark_quota,
            patch("app.google_media.generate_huggingface_image", side_effect=generate_scene),
            patch("app.provider_credentials.provider_candidates_for_role", return_value=["off"]),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                create_job_assets(
                    "job",
                    "topic",
                    "script",
                    tmp_path,
                    storyboard=[
                        {"stage": f"stage {index}", "visual_direction": "frame"}
                        for index in range(1, 7)
                    ],
                )

        mark_quota.assert_called_once_with("huggingface", quota_error)
        assert not list(tmp_path.glob("job-scene-*.png"))

    def test_huggingface_image_route_is_blocked_by_no_paid_mode(self, tmp_path):
        from app.google_media import create_job_assets

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch("app.provider_credentials.get_provider_for_role", return_value="huggingface"),
            patch("app.provider_credentials.no_paid_providers_enabled", return_value=True),
        ):
            with pytest.raises(RuntimeError, match="blocked while no-paid mode is ON"):
                create_job_assets("job", "topic", "script", tmp_path)

    def test_edge_tts_can_generate_voice_without_paid_provider(self, tmp_path):
        from app.google_media import create_job_assets

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch(
                "app.provider_credentials.provider_candidates_for_role",
                side_effect=lambda role: ["edge_tts"] if role == "voice" else ["off"],
            ),
            patch("app.google_media.generate_edge_tts_voiceover") as mock_edge_tts,
        ):
            assets = create_job_assets("job", "topic", "script", tmp_path)

        assert assets == {"voiceover": "job-voiceover.mp3"}
        mock_edge_tts.assert_called_once()

    def test_selected_edge_tts_failure_does_not_silently_drop_narration(self, tmp_path):
        from app.google_media import create_job_assets

        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch(
                "app.provider_credentials.provider_candidates_for_role",
                side_effect=lambda role: ["edge_tts"] if role == "voice" else ["off"],
            ),
            patch(
                "app.google_media.generate_edge_tts_voiceover",
                side_effect=RuntimeError("TTS unavailable"),
            ),
        ):
            with pytest.raises(RuntimeError, match="TTS unavailable"):
                create_job_assets("job", "topic", "script", tmp_path)

    def test_edge_tts_command_uses_supported_cli_flags(self, tmp_path):
        from app.google_media import generate_edge_tts_voiceover

        destination = tmp_path / "voice.mp3"
        with patch("app.google_media.subprocess.run") as run:
            generate_edge_tts_voiceover("हिंदी स्क्रिप्ट", destination)

        command = run.call_args.args[0]
        assert command[command.index("-v") + 1] == "hi-IN-MadhurNeural"
        assert command[command.index("-t") + 1] == "हिंदी स्क्रिप्ट"
        assert command[command.index("--write-media") + 1] == str(destination)
        assert run.call_args.kwargs.get("shell", False) is False

    def test_elevenlabs_voice_route_uses_configured_voice_id(self, tmp_path):
        import base64
        import httpx

        from app.google_media import generate_elevenlabs_voiceover

        response = httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"audio").decode("ascii"),
                "normalized_alignment": {
                    "characters": list("हिंदी आवाज"),
                    "character_start_times_seconds": [index * 0.1 for index in range(10)],
                    "character_end_times_seconds": [(index + 1) * 0.1 for index in range(10)],
                },
            },
            request=httpx.Request("POST", "https://api.elevenlabs.io"),
        )
        destination = tmp_path / "voice.mp3"
        with (
            patch("app.google_media.get_provider_secret", return_value="secret-key"),
            patch("app.provider_credentials.get_elevenlabs_voice_id", return_value="voice_123"),
            patch("app.google_media.httpx.post", return_value=response) as request,
        ):
            generate_elevenlabs_voiceover("हिंदी स्क्रिप्ट", destination)

        assert destination.read_bytes() == b"audio"
        assert request.call_args.args[0].endswith("/text-to-speech/voice_123/with-timestamps")
        assert request.call_args.kwargs["headers"]["xi-api-key"] == "secret-key"
        assert request.call_args.kwargs["json"]["model_id"] == "eleven_v4_turbo"
        assert destination.with_suffix(".timing.json").is_file()

    def test_no_paid_mode_ui_is_visible(self):
        from app.telegram import _provider_keyboard, _provider_message, _provider_role_keyboard

        callbacks = [
            button["callback_data"]
            for row in _provider_keyboard()["inline_keyboard"]
            for button in row
        ]
        assert "spending:toggle" in callbacks
        assert "No-paid providers: <b>ON</b>" in _provider_message()
        voice_callbacks = [
            button["callback_data"]
            for row in _provider_role_keyboard("voice")["inline_keyboard"]
            for button in row
        ]
        assert "elevenlabs_voice_id" in voice_callbacks
        assert "elevenlabs_tts_model" in voice_callbacks
        assert "voice_catalog" in voice_callbacks
        assert "fish_audio_reference_id" in voice_callbacks
        assert "fish_audio_model" in voice_callbacks
        assert "minimax_voice_id" in voice_callbacks
        assert "minimax_speech_model" in voice_callbacks
        assert "voice_benchmark" in voice_callbacks

    @pytest.mark.asyncio
    async def test_elevenlabs_voice_id_prompt_saves_configured_id(self):
        from app.provider_credentials import get_elevenlabs_voice_id
        from app.telegram import handle_update, _pending_elevenlabs_voice_id

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("elevenlabs_voice_id"))
        assert CHAT_ID in _pending_elevenlabs_voice_id

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("voice_123"))
        assert get_elevenlabs_voice_id() == "voice_123"
        assert CHAT_ID not in _pending_elevenlabs_voice_id
        assert "voice ID saved" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_fish_audio_reference_prompt_saves_configured_id(self):
        from app.provider_credentials import get_fish_audio_reference_id
        from app.telegram import handle_update, _pending_fish_audio_reference_id

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("fish_audio_reference_id"))
        assert CHAT_ID in _pending_fish_audio_reference_id

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("fish_voice_123"))
        assert get_fish_audio_reference_id() == "fish_voice_123"
        assert CHAT_ID not in _pending_fish_audio_reference_id
        assert "reference voice saved" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_paid_provider_toggle_requires_confirmation(self):
        from app.provider_credentials import (
            no_paid_providers_enabled,
            set_no_paid_providers_enabled,
        )
        from app.telegram import handle_update

        set_no_paid_providers_enabled(True)
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("spending:toggle"))

        assert no_paid_providers_enabled()
        assert "Allow billable providers?" in mock_send.call_args.args[1]

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("spending:paid"))

        assert not no_paid_providers_enabled()

    def test_image_generation_fails_over_on_quota_error_only(self, tmp_path):
        import httpx

        from app.google_media import create_job_assets

        request = httpx.Request("POST", "https://provider.example/generate")
        response = httpx.Response(429, request=request)
        quota_error = httpx.HTTPStatusError(
            "rate limit",
            request=request,
            response=response,
        )
        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch(
                "app.provider_credentials.provider_candidates_for_role",
                side_effect=lambda role: {
                    "image": ["google", "openai"],
                    "voice": ["off"],
                }[role],
            ),
            patch("app.google_media.generate_image", side_effect=quota_error),
            patch("app.provider_credentials.mark_provider_quota_error") as mock_cooldown,
            patch(
                "app.google_media._create_openai_assets",
                return_value={"image": "job-scene-1.png"},
            ) as mock_openai,
        ):
            assets = create_job_assets("job", "topic", "script", tmp_path)

        assert assets == {
            f"scene_{index}": f"job-scene-{index}.png" for index in range(1, 7)
        }
        assert mock_cooldown.call_count == 6
        assert mock_cooldown.call_args.args == ("google", quota_error)
        assert mock_openai.call_count == 6
        assert mock_openai.call_args.args == (
            "job",
            "topic",
            "script",
            tmp_path,
        )

    def test_image_generation_does_not_fail_over_on_permission_error(self, tmp_path):
        import httpx

        from app.google_media import create_job_assets

        request = httpx.Request("POST", "https://provider.example/generate")
        response = httpx.Response(
            403,
            json={"error": {"status": "PERMISSION_DENIED"}},
            request=request,
        )
        permission_error = httpx.HTTPStatusError(
            "permission denied",
            request=request,
            response=response,
        )
        with (
            patch("app.provider_credentials.media_generation_enabled", return_value=True),
            patch(
                "app.provider_credentials.provider_candidates_for_role",
                side_effect=lambda role: {
                    "image": ["google", "openai"],
                    "voice": ["off"],
                }[role],
            ),
            patch("app.google_media.generate_image", side_effect=permission_error),
            patch("app.provider_credentials.mark_provider_quota_error") as mock_cooldown,
            pytest.raises(httpx.HTTPStatusError),
        ):
            create_job_assets("job", "topic", "script", tmp_path)

        mock_cooldown.assert_not_called()

    def test_portable_backup_round_trip_restores_database_and_media(self, tmp_path, monkeypatch):
        import shutil

        from app.config import get_settings
        from app.db import Base, FactorySetting, SessionLocal, engine
        from app.discovery import add_trend
        from app.backups import create_backup_archive, restore_backup_archive

        media_root = tmp_path / "factory-media"
        monkeypatch.setenv("MEDIA_ROOT", str(media_root))
        get_settings.cache_clear()
        try:
            media_root.mkdir()
            (media_root / "approved-video.mp4").write_bytes(b"video-data")
            add_trend("Backup migration test")
            with SessionLocal() as session:
                session.add(FactorySetting(key="backup:migration", value="preserved"))
                session.commit()

            archive_path = create_backup_archive()
            try:
                with engine.begin() as connection:
                    for table in reversed(Base.metadata.sorted_tables):
                        connection.execute(table.delete())
                shutil.rmtree(media_root)

                result = restore_backup_archive(archive_path)

                assert result["rows"] >= 2
                assert result["media_files"] == 1
                assert (media_root / "approved-video.mp4").read_bytes() == b"video-data"
                with SessionLocal() as session:
                    restored_setting = session.get(FactorySetting, "backup:migration")
                    assert restored_setting.value == "preserved"
            finally:
                archive_path.unlink(missing_ok=True)
        finally:
            get_settings.cache_clear()

    def test_portable_backup_file_is_encrypted_and_rejects_wrong_passphrase(self, tmp_path):
        from scripts.factory_backup import _decrypt_backup, _encrypt_response

        class FakeResponse:
            def iter_bytes(self, _chunk_size):
                return iter((b"factory backup ", b"payload"))

        encrypted = tmp_path / "factory.hcfb"
        decrypted = tmp_path / "factory.tar.gz"
        _encrypt_response(FakeResponse(), encrypted, "strong passphrase 123")
        assert encrypted.read_bytes() != b"factory backup payload"

        _decrypt_backup(encrypted, decrypted, "strong passphrase 123")
        assert decrypted.read_bytes() == b"factory backup payload"

        with pytest.raises(ValueError, match="Wrong passphrase"):
            _decrypt_backup(encrypted, decrypted, "incorrect passphrase")


# ── Update handler tests ───────────────────────────────────────────────
class TestHandleUpdate:
    @pytest.mark.asyncio
    async def test_custom_creator_part_selection_sets_pending_count(self):
        from app.telegram import handle_update, _pending_custom_part_count

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("custom_parts:7"))

        assert _pending_custom_part_count[CHAT_ID] == 7
        assert "7" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_callback_acknowledges_with_working_indicator(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock),
            patch("app.telegram.answer_callback", new_callable=AsyncMock) as mock_answer,
        ):
            await handle_update(_callback_update("status"))
            mock_answer.assert_awaited_once_with("cb_123", "⏳ Working…")

    @pytest.mark.asyncio
    async def test_start_command(self):
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/start"))
            mock_send.assert_called_once()
            text = mock_send.call_args[0][1]
            assert "Content Factory" in text

    @pytest.mark.asyncio
    async def test_help_command(self):
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/help"))
            mock_send.assert_called_once()
            text = mock_send.call_args[0][1]
            assert "/generate" in text

    @pytest.mark.asyncio
    async def test_status_command(self):
        from app.telegram import handle_update
        from app.services import set_paused

        set_paused(True)
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/status"))
            mock_send.assert_called_once()
            text = mock_send.call_args[0][1]
            assert "PAUSED" in text

    @pytest.mark.asyncio
    async def test_queue_command(self):
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/queue"))
            mock_send.assert_called_once()
            text = mock_send.call_args[0][1]
            assert "Queue" in text

    @pytest.mark.asyncio
    async def test_pause_command(self):
        from app.telegram import handle_update
        from app.services import is_paused, set_paused

        set_paused(False)
        assert not is_paused()

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/pause"))
            assert is_paused()
            text = mock_send.call_args[0][1]
            assert "paused" in text.lower()

    @pytest.mark.asyncio
    async def test_resume_command(self):
        from app.telegram import handle_update
        from app.services import is_paused

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/resume"))
            assert not is_paused()
            text = mock_send.call_args[0][1]
            assert "resumed" in text.lower()

    @pytest.mark.asyncio
    async def test_generate_command(self):
        from app.hq import set_publish_target
        from app.telegram import _background_tasks, handle_update
        from app.services import list_jobs

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.send_progress_message", new_callable=AsyncMock, return_value=99),
            patch("app.telegram.finish_progress_message", new_callable=AsyncMock) as mock_finish,
        ):
            set_publish_target("both")
            await handle_update(_text_update("/generate भारत का इतिहास", update_id=40))
            for task in list(_background_tasks):
                await task
                _background_tasks.discard(task)

            final_text = ""
            for call in mock_finish.call_args_list:
                if "Script review" in str(call.args[2]):
                    final_text = str(call.args[2])
            if not final_text and mock_send.call_args_list:
                final_text = str(mock_send.call_args_list[-1].args[1])

            assert final_text, "the script review screen was never sent"
            assert "No voice, image, video, music, or rendering credits" in final_text
            assert "भारत का इतिहास" in final_text
            assert "Approve this exact version" in final_text
            assert "40%" not in final_text
            assert list_jobs()[0].platform == "both"
            assert list_jobs()[0].status == "awaiting_script_approval"

    @pytest.mark.asyncio
    async def test_clip_studio_accepts_vod_url(self, monkeypatch):
        from cryptography.fernet import Fernet
        from app.config import get_settings
        from app.clipping import list_clip_jobs
        from app.telegram import handle_update, _pending_clip_source

        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
        get_settings.cache_clear()
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("clip_new:vod"))
        assert _pending_clip_source[CHAT_ID] == ("vod", None)

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("https://video.example/watch?v=123", update_id=88))
        assert CHAT_ID not in _pending_clip_source
        assert list_clip_jobs()[0].source_kind == "vod"
        assert "Clip job queued" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_generate_same_topic_with_new_updates_creates_new_jobs(self):
        from app.services import list_jobs
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock):
            await handle_update(_text_update("/generate भारत का इतिहास", update_id=41))
            await handle_update(_text_update("/generate भारत का इतिहास", update_id=42))

        jobs = list_jobs()
        assert len(jobs) == 2
        assert jobs[0].id != jobs[1].id
        assert all(job.topic == "भारत का इतिहास" for job in jobs)

    @pytest.mark.asyncio
    async def test_duplicate_generate_update_does_not_generate_twice(self):
        from app.services import generate_content, list_jobs
        from app.telegram import _background_tasks, handle_update

        update = _text_update("/generate भारत का इतिहास", update_id=43)
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.send_progress_message", new_callable=AsyncMock, return_value=99),
            patch("app.telegram.finish_progress_message", new_callable=AsyncMock),
            patch("app.telegram.generate_content", wraps=generate_content) as mock_generate,
        ):
            await handle_update(update)
            for task in list(_background_tasks):
                await task
                _background_tasks.discard(task)
            await handle_update(update)

        assert len(list_jobs()) == 1
        mock_generate.assert_called_once()
        assert "already processed" in mock_send.call_args.args[1].lower()

    @pytest.mark.asyncio
    async def test_generate_empty_topic_rejected(self):
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/generate  "))
            text = mock_send.call_args[0][1]
            # Empty topic after strip — handler sends 'provide a topic' or falls to unrecognised
            assert "topic" in text.lower() or "not recognised" in text.lower()

    @pytest.mark.asyncio
    async def test_unknown_command_shows_help(self):
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/nonexistent"))
            text = mock_send.call_args[0][1]
            assert "not recognised" in text.lower() or "/help" in text or "/menu" in text

    @pytest.mark.asyncio
    async def test_non_owner_rejected(self):
        from app.telegram import handle_update

        update = _text_update("/start")
        update["message"]["from"]["id"] = 99999
        update["message"]["chat"]["id"] = 99999
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(update)
            mock_send.assert_awaited_once_with(99999, "🚫 DO NOT DISTURB ME BETA !!")

    @pytest.mark.asyncio
    async def test_non_owner_gets_only_one_notice_per_day(self):
        from app.telegram import handle_update

        update = _text_update("/start")
        update["message"]["from"]["id"] = 99999
        update["message"]["chat"]["id"] = 99999
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(update)
            await handle_update(update)
            mock_send.assert_awaited_once_with(99999, "🚫 DO NOT DISTURB ME BETA !!")

    @pytest.mark.asyncio
    async def test_owner_command_in_group_is_ignored(self):
        from app.telegram import handle_update

        update = {
            "message": {
                "text": "/status",
                "from": {"id": OWNER_ID},
                "chat": {"id": -10012345, "type": "supergroup"},
            }
        }
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(update)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_owner_group_is_ignored_without_notice(self):
        from app.telegram import handle_update

        update = {
            "message": {
                "text": "/start",
                "from": {"id": 99999},
                "chat": {"id": -10012345, "type": "group"},
            }
        }
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(update)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_owner_callback_is_acknowledged_and_notified(self):
        from app.telegram import handle_update

        update = _callback_update("status")
        update["callback_query"]["from"]["id"] = 99999
        update["callback_query"]["message"]["chat"]["id"] = 99999
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock) as mock_answer,
        ):
            await handle_update(update)
            mock_send.assert_awaited_once_with(99999, "🚫 DO NOT DISTURB ME BETA !!")
            mock_answer.assert_awaited_once_with("cb_123")

    @pytest.mark.asyncio
    async def test_callback_status(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("status"))
            text = mock_send.call_args[0][1]
            assert "Dashboard" in text or "PAUSED" in text

    @pytest.mark.asyncio
    async def test_youtube_connect_shows_setup_guide_when_oauth_not_configured(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.connection_status", return_value={
                "configured": False,
                "authorized": False,
            }),
            patch("app.telegram.create_authorization_url") as mock_auth_url,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("youtube_auth"))

        sent_text = mock_send.call_args.args[1]
        markup = mock_send.call_args.args[2]
        assert "YouTube setup" in sent_text
        assert "Step 5 of 5" in sent_text
        assert "YOUTUBE_CLIENT_ID" in sent_text
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        assert any(button.get("callback_data") == "youtube_step:4" for button in buttons)
        assert any(button.get("url") == "https://railway.com/dashboard" for button in buttons)
        mock_auth_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_youtube_setup_button_displays_steps(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("youtube_setup"))

        assert "Step 1 of 5" in mock_send.call_args.args[1]
        assert any(
            button.get("callback_data") == "youtube_step:2"
            for row in mock_send.call_args.args[2]["inline_keyboard"]
            for button in row
        )

    @pytest.mark.asyncio
    async def test_instagram_setup_button_displays_guide(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("instagram_setup_guide"))

        assert "Step 1 of 6" in mock_send.call_args.args[1]
        buttons = [
            button
            for row in mock_send.call_args.args[2]["inline_keyboard"]
            for button in row
        ]
        assert any(button.get("callback_data") == "instagram_step:2" for button in buttons)

    @pytest.mark.asyncio
    async def test_instagram_id_button_prompts_and_saves_id(self):
        from app.platform_credentials import get_platform_value
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("instagram_id"))
            await handle_update(_text_update("17841400000000000"))

        assert get_platform_value("instagram", "user_id") == "17841400000000000"
        assert "account ID saved" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_instagram_token_is_deleted_before_exchange_and_saved_encrypted(self, monkeypatch):
        from app.config import get_settings
        from app.telegram import handle_update

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        get_settings.cache_clear()
        calls = []

        async def delete(_chat_id, message_id):
            calls.append(("delete", message_id))

        async def exchange(token):
            calls.append(("exchange", token))
            return 60 * 86400

        message = _text_update("short-lived-token")
        message["message"]["message_id"] = 456
        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.delete_message", side_effect=delete),
            patch("app.platform_credentials.exchange_instagram_token", side_effect=exchange),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("instagram_token"))
            await handle_update(message)

        assert calls == [("delete", 456), ("exchange", "short-lived-token")]
        assert "saved encrypted" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_instagram_cannot_be_enabled_before_setup_is_complete(self):
        from app.platform_credentials import platform_enabled
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.platform_credentials.set_platform_enabled") as mock_set_enabled,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("instagram:enable"))

        mock_set_enabled.assert_not_called()
        assert "not ready yet" in mock_send.call_args.args[1]
        assert platform_enabled("instagram", False) is False

    @pytest.mark.asyncio
    async def test_youtube_enable_requires_a_connected_account(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.connection_status", return_value={"authorized": False}),
            patch("app.platform_credentials.set_platform_enabled") as mock_set_enabled,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("youtube:enable"))

        mock_set_enabled.assert_not_called()
        assert "Connect a YouTube account first" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_accounts_callback_shows_management_status_and_buttons(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("accounts"))

        text, keyboard = mock_send.call_args.args[1:3]
        assert "Channel accounts" in text
        assert "YouTube" in text and "Instagram" in text
        assert any(
            button.get("callback_data") == "instagram_disconnect_confirm"
            for row in keyboard["inline_keyboard"]
            for button in row
        )

    @pytest.mark.asyncio
    async def test_youtube_verify_shows_channel_name(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch(
                "app.youtube_oauth.get_authorized_channel",
                return_value={"id": "UC123", "title": "Hindi Learning"},
            ),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("youtube_verify"))

        assert "Hindi Learning" in mock_send.call_args.args[1]
        assert "UC123" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_instagram_verify_shows_account_name(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch(
                "app.platform_credentials.verify_instagram_account",
                return_value={"id": "17841400000000000", "username": "my_channel"},
            ),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("instagram_verify"))

        assert "@my_channel" in mock_send.call_args.args[1]
        assert "17841400000000000" in mock_send.call_args.args[1]

    @pytest.mark.asyncio
    async def test_callback_queue(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("queue"))
            text = mock_send.call_args[0][1]
            assert "Queue" in text

    @pytest.mark.asyncio
    async def test_callback_menu(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("menu"))
            text = mock_send.call_args[0][1]
            assert "Content Factory" in text

    @pytest.mark.asyncio
    async def test_callback_refresh(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("refresh"))
            text = mock_send.call_args[0][1]
            assert "Dashboard" in text or "PAUSED" in text

    @pytest.mark.asyncio
    async def test_malformed_update_ignored(self):
        from app.telegram import handle_update

        # No user_id / chat_id — should return silently
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update({})
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_in_handler_sends_safe_message(self):
        from app.telegram import handle_update

        with (
            patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send,
            patch("app.telegram.approve_job", side_effect=RuntimeError("DB connection lost")),
            patch("app.telegram.answer_callback", new_callable=AsyncMock),
        ):
            await handle_update(_callback_update("approve:fake-id"))
            text = mock_send.call_args[0][1]
            # Should NOT contain the raw error
            assert "DB connection lost" not in text
            assert "internal error" in text.lower()
