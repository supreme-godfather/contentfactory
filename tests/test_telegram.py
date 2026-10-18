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
def isolated_database(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_OWNER_IDS", "12345")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import init_db

    init_db()
    yield
    get_settings.cache_clear()


OWNER_ID = 12345
CHAT_ID = 12345


def _text_update(text: str) -> dict:
    """Build a minimal Telegram text message update."""
    return {
        "message": {
            "text": text,
            "from": {"id": OWNER_ID},
            "chat": {"id": CHAT_ID},
        }
    }


def _callback_update(data: str) -> dict:
    """Build a minimal Telegram callback_query update."""
    return {
        "callback_query": {
            "id": "cb_123",
            "data": data,
            "from": {"id": OWNER_ID},
            "message": {
                "chat": {"id": CHAT_ID},
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


# ── Keyboard layout tests ──────────────────────────────────────────────
class TestKeyboards:
    def test_main_keyboard_has_rows(self):
        from app.telegram import _keyboard

        kb = _keyboard()
        rows = kb["inline_keyboard"]
        assert len(rows) == 4

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

    def test_job_keyboard_has_actions(self):
        from app.telegram import _job_keyboard

        kb = _job_keyboard("test-job-id-1234")
        rows = kb["inline_keyboard"]
        all_texts = [btn["text"] for row in rows for btn in row]
        assert any("Approve" in t for t in all_texts)
        assert any("Publish" in t for t in all_texts)
        assert any("Quality" in t for t in all_texts)
        assert any("Back" in t for t in all_texts)

    def test_back_keyboard(self):
        from app.telegram import _back_keyboard

        kb = _back_keyboard()
        assert len(kb["inline_keyboard"]) == 1
        assert kb["inline_keyboard"][0][0]["callback_data"] == "menu"


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
        for cmd in ["/generate", "/approve", "/publish", "/status", "/queue", "/trends", "/analytics", "/pause", "/resume", "/menu"]:
            assert cmd in msg, f"Missing {cmd} in help message"

    def test_controls_message(self):
        from app.telegram import _build_controls_message

        msg = _build_controls_message()
        assert "/generate" in msg
        assert "/approve" in msg


# ── Update handler tests ───────────────────────────────────────────────
class TestHandleUpdate:
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
        from app.telegram import handle_update

        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(_text_update("/generate भारत का इतिहास"))
            mock_send.assert_called_once()
            text = mock_send.call_args[0][1]
            assert "Preview created" in text
            assert "भारत का इतिहास" in text

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

        update = {
            "message": {
                "text": "/start",
                "from": {"id": 99999},
                "chat": {"id": 99999},
            }
        }
        with patch("app.telegram.send_message", new_callable=AsyncMock) as mock_send:
            await handle_update(update)
            # Error message should be sent, not the start message
            text = mock_send.call_args[0][1]
            assert "not completed" in text.lower() or "error" in text.lower()

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
