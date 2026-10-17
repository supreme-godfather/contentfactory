import logging
import asyncio
import re
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
            [_button("📊 Status", "status"), _button("📥 Queue", "queue")],
            [_button("🔥 Trends", "trends"), _button("📈 Analytics", "analytics")],
            [_button("⏸ Pause", "pause"), _button("▶ Resume", "resume")],
            [_button("❓ Help", "help")],
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
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
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
                return
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:300]
                logger.warning("Telegram webhook attempt %s failed: %s", attempt, detail)
            except httpx.HTTPError:
                logger.exception("Telegram webhook attempt %s failed", attempt)
            if attempt < 5:
                await asyncio.sleep(attempt * 5)


async def handle_update(update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    callback = update.get("callback_query")
    source = message if message else (callback or {}).get("message", {})
    sender = message.get("from") if message else (callback or {}).get("from", {})
    user_id = sender.get("id")
    chat_id = source.get("chat", {}).get("id")
    if not isinstance(user_id, int) or not isinstance(chat_id, int):
        return
    require_owner(user_id)

    if callback:
        data = callback.get("data", "")
        if data == "pause":
            set_paused(True)
            await send_message(chat_id, "⏸ Factory paused. No publication will run.")
        elif data == "resume":
            set_paused(False)
            await send_message(chat_id, "▶ Factory resumed. Real publishing remains disabled in this build.")
        elif data == "queue":
            jobs = list_jobs()
            lines = [f"{job.id[:8]} · {job.status} · {job.platform} · {job.topic}" for job in jobs]
            await send_message(chat_id, "\n".join(lines) if lines else "Queue is empty.")
        elif data == "trends":
            items = list_trends()
            lines = [f"{item.total_score}/100 · {item.topic} · {item.source}" for item in items]
            await send_message(chat_id, "\n".join(lines) if lines else "No trend candidates yet.", _keyboard())
        elif data == "analytics":
            items = analytics_summary()
            lines = [
                f"{item['platform']} · {item['content_id']} · {item['views']} views · {item['engagement_rate']}%"
                for item in items
            ]
            await send_message(chat_id, "\n".join(lines) if lines else "No analytics snapshots yet.", _keyboard())
        elif data.startswith("approve:"):
            job = approve_job(data.split(":", 1)[1], user_id)
            await send_message(chat_id, f"✅ Approved {job.id}.", _job_keyboard(job.id))
        elif data.startswith("publish:"):
            job = publish_job(data.split(":", 1)[1])
            await send_message(chat_id, f"📤 Mock-published {job.id}.", _keyboard())
        else:
            await send_message(chat_id, "Use /help for available controls.", _keyboard())
        return

    text = (message.get("text") or "").strip()
    if text == "/start":
        await send_message(chat_id, "Content Factory is ready and paused.", _keyboard())
    elif text == "/help":
        await send_message(chat_id, "/status, /queue, /trends, /analytics, /generate <topic>, /approve <job-id>, /publish <job-id>, /pause, /resume", _keyboard())
    elif text == "/status":
        settings = get_settings()
        await send_message(chat_id, f"Paused: {is_paused()}\nMock publishing: {settings.mock_publishing}\nDaily limit: {settings.max_daily_publications}", _keyboard())
    elif text == "/queue":
        jobs = list_jobs()
        await send_message(chat_id, "\n".join(f"{job.id[:8]} · {job.status} · {job.topic}" for job in jobs) or "Queue is empty.", _keyboard())
    elif text == "/trends":
        items = list_trends()
        await send_message(chat_id, "\n".join(f"{item.total_score}/100 · {item.topic}" for item in items) or "No trend candidates yet.", _keyboard())
    elif text == "/analytics":
        items = analytics_summary()
        await send_message(chat_id, "\n".join(f"{item['platform']} · {item['views']} views · {item['engagement_rate']}%" for item in items) or "No analytics snapshots yet.", _keyboard())
    elif text.startswith("/generate "):
        job = create_job(text[10:], "both")
        job = generate_content(job.id)
        await send_message(chat_id, f"Created preview job {job.id} for approval.\nTopic: {job.topic}", _job_keyboard(job.id))
    elif text.startswith("/approve "):
        job_id = text[9:].strip()
        job = approve_job(job_id, user_id)
        await send_message(chat_id, f"Approved job {job.id}. It will publish only after the factory is resumed.", _keyboard())
    elif text.startswith("/publish "):
        job = publish_job(text[9:].strip())
        await send_message(chat_id, f"Mock-published job {job.id}. Real platform publishing is not enabled.", _keyboard())
    elif text == "/pause":
        set_paused(True)
        await send_message(chat_id, "⏸ Factory paused.", _keyboard())
    elif text == "/resume":
        set_paused(False)
        await send_message(chat_id, "▶ Factory resumed; mock mode is still active.", _keyboard())
    else:
        await send_message(chat_id, "Unknown command. Use /help.", _keyboard())
