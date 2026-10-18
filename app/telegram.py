import logging
import asyncio
import re
import html
from typing import Any

import httpx

from .config import get_settings
from .security import require_owner
from .discovery import analytics_summary, list_trends
from .services import approve_job, create_job, generate_content, is_paused, list_jobs, publish_job, set_paused

logger = logging.getLogger(__name__)


def _button(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def _keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [_button("✨ Create", "create"), _button("📥 Review queue", "queue")],
            [_button("📊 System status", "status"), _button("🔥 Trend radar", "trends")],
            [_button("📈 Analytics", "analytics"), _button("⚙️ Controls", "controls")],
            [_button("⏸ Pause", "pause"), _button("▶ Resume", "resume")],
        ]
    }


def _job_keyboard(job_id: str) -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [_button("✅ Approve", f"approve:{job_id}"), _button("❌ Reject", f"reject:{job_id}")],
            [_button("📤 Publish", f"publish:{job_id}")],
        ]
    }


def _message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload


async def send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=_message(chat_id, text, reply_markup))
        response.raise_for_status()


async def answer_callback(callback_id: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not callback_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json={"callback_query_id": callback_id})
        response.raise_for_status()


async def configure_webhook() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.public_base_url:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    payload: dict[str, str] = {
        "url": f"{settings.public_base_url.rstrip('/')}/telegram/webhook",
        "drop_pending_updates": "false",
    }
    if settings.telegram_webhook_secret:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", settings.telegram_webhook_secret):
            payload["secret_token"] = settings.telegram_webhook_secret
        else:
            logger.error("Telegram webhook secret has illegal characters; registering without a secret")
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(1, 6):
            try:
                response = await client.post(url, data=payload)
                response.raise_for_status()
                logger.info("Telegram webhook registered")
                break
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:300]
                logger.warning("Telegram webhook attempt %s failed: %s", attempt, detail)
            except httpx.HTTPError:
                logger.exception("Telegram webhook attempt %s failed", attempt)
            if attempt < 5:
                await asyncio.sleep(attempt * 5)
        commands_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setMyCommands"
        commands = {
            "commands": [
                {"command": "start", "description": "Open the private command center"},
                {"command": "status", "description": "View factory health and safety mode"},
                {"command": "queue", "description": "Review pending content"},
                {"command": "trends", "description": "View trend radar"},
                {"command": "analytics", "description": "View performance analytics"},
                {"command": "generate", "description": "Create a Hindi preview"},
                {"command": "pause", "description": "Pause automation"},
                {"command": "resume", "description": "Resume automation"},
                {"command": "help", "description": "Show all commands"},
            ]
        }
        try:
            response = await client.post(commands_url, json=commands)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Telegram command menu registration failed")


async def handle_update(update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    callback = update.get("callback_query")
    source = message if message else (callback or {}).get("message", {})
    sender = message.get("from") if message else (callback or {}).get("from", {})
    user_id = sender.get("id")
    chat_id = source.get("chat", {}).get("id")
    if not isinstance(user_id, int) or not isinstance(chat_id, int):
        return
    try:
        require_owner(user_id)
        if callback:
            await answer_callback(str(callback.get("id", "")))
            data = callback.get("data", "")
            if data == "pause":
                set_paused(True)
                await send_message(chat_id, "⏸ <b>Factory paused</b>\nNo publication will run.", _keyboard())
            elif data == "resume":
                set_paused(False)
                await send_message(chat_id, "▶ <b>Factory resumed</b>\nPublishing remains approval-gated.", _keyboard())
            elif data in {"create", "controls", "help"}:
                await send_message(chat_id, "✨ <b>Command center</b>\n\nUse /generate &lt;topic&gt; to create a Hindi preview.\nUse /approve &lt;job-id&gt; only after review.\nUse /publish &lt;job-id&gt; for the current safe publisher mode.", _keyboard())
            elif data == "status":
                settings = get_settings()
                await send_message(chat_id, f"📊 <b>System status</b>\n\nPaused: {is_paused()}\nMock mode: {settings.mock_publishing}\nDaily quota: {settings.max_daily_publications}", _keyboard())
            elif data == "queue":
                jobs = list_jobs()
                lines = [f"• <code>{html.escape(job.id[:8])}</code> · {job.status} · {html.escape(job.topic)}" for job in jobs]
                await send_message(chat_id, "📥 <b>Review queue</b>\n\n" + ("\n".join(lines) if lines else "Queue is empty."), _keyboard())
            elif data == "trends":
                items = list_trends()
                lines = [f"• {item.total_score}/100 · {html.escape(item.topic)} · {html.escape(item.source)}" for item in items]
                await send_message(chat_id, "🔥 <b>Trend radar</b>\n\n" + ("\n".join(lines) if lines else "No trend candidates yet."), _keyboard())
            elif data == "analytics":
                items = analytics_summary()
                lines = [f"• {html.escape(str(item['platform']))} · {item['views']} views · {item['engagement_rate']}% engagement" for item in items]
                await send_message(chat_id, "📈 <b>Analytics</b>\n\n" + ("\n".join(lines) if lines else "No analytics snapshots yet."), _keyboard())
            elif data.startswith("approve:"):
                job = approve_job(data.split(":", 1)[1], user_id)
                await send_message(chat_id, f"✅ Approved <code>{job.id}</code>.\nThe current content version is locked.", _job_keyboard(job.id))
            elif data.startswith("publish:"):
                job = publish_job(data.split(":", 1)[1])
                await send_message(chat_id, f"📤 Published through the configured safe publisher: <code>{job.id}</code>.", _keyboard())
            return

        text = (message.get("text") or "").strip()
        if text in {"/start", "/menu"}:
            await send_message(chat_id, "🎬 <b>Content Factory Command Center</b>\n\nYour private Hindi content engine is online.\n\n🔒 Approval required\n🧠 Trend signals, not copying\n🛡️ Real publishing stays disabled until authorized\n\nChoose an action below.", _keyboard())
        elif text == "/help":
            await send_message(chat_id, "📚 <b>Commands</b>\n\n/status · /queue · /trends · /analytics\n/generate &lt;topic&gt;\n/approve &lt;job-id&gt;\n/publish &lt;job-id&gt;\n/pause · /resume", _keyboard())
        elif text == "/status":
            settings = get_settings()
            await send_message(chat_id, f"📊 <b>System status</b>\n\nPaused: {is_paused()}\nMock mode: {settings.mock_publishing}\nDaily quota: {settings.max_daily_publications}", _keyboard())
        elif text == "/queue":
            jobs = list_jobs()
            await send_message(chat_id, "📥 <b>Review queue</b>\n\n" + ("\n".join(f"• {job.id[:8]} · {job.status} · {html.escape(job.topic)}" for job in jobs) or "Queue is empty."), _keyboard())
        elif text == "/trends":
            items = list_trends()
            await send_message(chat_id, "🔥 <b>Trend radar</b>\n\n" + ("\n".join(f"• {item.total_score}/100 · {html.escape(item.topic)}" for item in items) or "No trend candidates yet."), _keyboard())
        elif text == "/analytics":
            items = analytics_summary()
            await send_message(chat_id, "📈 <b>Analytics</b>\n\n" + ("\n".join(f"• {item['platform']} · {item['views']} views · {item['engagement_rate']}%" for item in items) or "No analytics snapshots yet."), _keyboard())
        elif text.startswith("/generate "):
            job = generate_content(create_job(text[10:], "both").id)
            await send_message(chat_id, f"✨ <b>Preview created</b>\n\nTopic: {html.escape(job.topic)}\nJob: <code>{job.id}</code>\n\nReview it before approval.", _job_keyboard(job.id))
        elif text.startswith("/approve "):
            job = approve_job(text[9:].strip(), user_id)
            await send_message(chat_id, f"✅ Approved <code>{job.id}</code>.\nIt remains gated until the factory is resumed.", _keyboard())
        elif text.startswith("/publish "):
            job = publish_job(text[9:].strip())
            await send_message(chat_id, f"📤 Publication completed by the configured publisher: <code>{job.id}</code>.", _keyboard())
        elif text == "/pause":
            set_paused(True)
            await send_message(chat_id, "⏸ <b>Factory paused</b>.", _keyboard())
        elif text == "/resume":
            set_paused(False)
            await send_message(chat_id, "▶ <b>Factory resumed</b>; approval gates remain active.", _keyboard())
        else:
            await send_message(chat_id, "I did not recognize that command. Use /menu.", _keyboard())
    except Exception as exc:
        logger.exception("Telegram update failed")
        await send_message(chat_id, f"⚠️ <b>Action not completed</b>\n\n{html.escape(str(exc))}\n\nNo publication was triggered.", _keyboard())
