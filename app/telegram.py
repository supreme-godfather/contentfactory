import logging
import asyncio
import re
import html
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import get_settings
from .security import require_owner
from .discovery import analytics_summary, list_trends
from .services import approve_job, create_job, generate_content, is_paused, list_jobs, publish_job, set_paused
from .youtube_oauth import connection_status, create_authorization_url

logger = logging.getLogger(__name__)

# ── Emoji constants for visual consistency ──────────────────────────────
_STATUS_EMOJI = {
    "queued": "🟡",
    "awaiting_approval": "🟠",
    "approved": "🟢",
    "rendering": "🔵",
    "published": "✅",
    "failed": "🔴",
}

_STAGE_LABEL = {
    "topic": "Topic selection",
    "quality_check": "Quality review",
    "render": "Video render",
    "published": "Published",
}


# ── Button helpers ──────────────────────────────────────────────────────
def _button(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def _keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Dynamic main menu keyboard — shows the correct pause/resume state."""
    paused = is_paused()
    toggle = _button("▶️ Resume factory", "resume") if paused else _button("⏸️ Pause factory", "pause")
    return {
        "inline_keyboard": [
            [_button("✨ New content", "create"), _button("📥 Review queue", "queue")],
            [_button("📊 Status dashboard", "status"), _button("🔥 Trend radar", "trends")],
            [_button("📈 Analytics", "analytics"), _button("🔗 Connect YouTube", "youtube_auth")],
            [toggle, _button("🔄 Refresh", "refresh")],
        ]
    }


def _job_keyboard(job_id: str) -> dict[str, list[list[dict[str, str]]]]:
    """Rich action keyboard for a single job."""
    short = job_id[:8]
    return {
        "inline_keyboard": [
            [
                _button("✅ Approve", f"approve:{job_id}"),
                _button("❌ Reject", f"reject:{job_id}"),
            ],
            [
                _button("📤 Publish now", f"publish:{job_id}"),
                _button("🔍 Quality check", f"quality:{job_id}"),
            ],
            [_button("🔙 Back to menu", "menu")],
        ]
    }


def _back_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": [[_button("🔙 Back to menu", "menu")]]}


# ── Message formatting helpers ──────────────────────────────────────────
def _divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━"


def _score_bar(score: int, width: int = 10) -> str:
    """Render a visual bar like ██████░░░░ 60/100."""
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled) + f" {score}/100"


def _format_job_card(job: Any, *, compact: bool = False) -> str:
    """Format a single job as a rich Telegram card."""
    emoji = _STATUS_EMOJI.get(job.status, "⚪")
    stage = _STAGE_LABEL.get(job.stage, job.stage)
    short_id = job.id[:8]

    if compact:
        return f"{emoji} <code>{html.escape(short_id)}</code>  {html.escape(job.topic)}"

    lines = [
        f"{emoji} <b>{html.escape(job.topic)}</b>",
        f"",
        f"   🆔 <code>{html.escape(job.id)}</code>",
        f"   📋 Status: <b>{job.status.replace('_', ' ').title()}</b>",
        f"   🎯 Stage: {stage}",
        f"   🏷️ Brand: {html.escape(job.brand)}",
        f"   📱 Platform: {job.platform}",
        f"   📝 Version: v{job.content_version}",
    ]
    if job.approval_version is not None:
        lines.append(f"   ✅ Approved: v{job.approval_version}")
    if job.error_message:
        lines.append(f"   ⚠️ Error: {html.escape(job.error_message[:120])}")
    return "\n".join(lines)


def _safe_error_text(exc: Exception) -> str:
    """Return a user-safe error description, never leaking internals."""
    msg = str(exc)
    # Only pass through known, safe ValueError messages from our own services
    safe_prefixes = (
        "Job not found",
        "Factory is paused",
        "Job requires approval",
        "Job is not",
        "Quality gate blocked",
        "Only queued",
        "Topic must contain",
        "Platform must be",
    )
    for prefix in safe_prefixes:
        if msg.startswith(prefix):
            return html.escape(msg[:200])
    return "An internal error occurred. No content was published."


# ── Telegram API helpers ────────────────────────────────────────────────
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
                {"command": "start", "description": "🏠 Open the command center"},
                {"command": "status", "description": "📊 View system dashboard"},
                {"command": "queue", "description": "📥 Review content queue"},
                {"command": "trends", "description": "🔥 View trend radar"},
                {"command": "analytics", "description": "📈 Performance analytics"},
                {"command": "generate", "description": "✨ Create a Hindi preview"},
                {"command": "pause", "description": "⏸️ Pause the factory"},
                {"command": "resume", "description": "▶️ Resume the factory"},
                {"command": "help", "description": "📚 Show all commands"},
            ]
        }
        try:
            response = await client.post(commands_url, json=commands)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Telegram command menu registration failed")


# ── Response builders ───────────────────────────────────────────────────
def _build_start_message() -> str:
    return (
        f"🎬 <b>Content Factory</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"Your private Hindi content engine is online.\n"
        f"\n"
        f"  🔒  Approval-gated publishing\n"
        f"  🧠  Trend-driven, original content\n"
        f"  🛡️  Real platforms disabled until authorized\n"
        f"  🎯  AI-powered Hindi explainers\n"
        f"\n"
        f"{_divider()}\n"
        f"Choose an action below to get started."
    )


def _build_status_message() -> str:
    settings = get_settings()
    paused = is_paused()
    state_icon = "🔴" if paused else "🟢"
    state_text = "PAUSED" if paused else "RUNNING"
    mock_icon = "🧪" if settings.mock_publishing else "🚀"
    mock_text = "Mock mode" if settings.mock_publishing else "Live mode"
    yt_icon = "✅" if settings.youtube_enabled else "❌"
    ig_icon = "✅" if settings.instagram_enabled else "❌"

    return (
        f"📊 <b>System Dashboard</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"  {state_icon}  Factory: <b>{state_text}</b>\n"
        f"  {mock_icon}  Publisher: <b>{mock_text}</b>\n"
        f"\n"
        f"  <b>Platform status</b>\n"
        f"  {yt_icon}  YouTube\n"
        f"  {ig_icon}  Instagram\n"
        f"\n"
        f"  <b>Limits</b>\n"
        f"  📤  Daily quota: {settings.max_daily_publications}\n"
        f"  ⚙️  Max concurrent: {settings.max_concurrent_jobs}\n"
        f"  💾  Storage cap: {settings.max_storage_gb} GB\n"
        f"\n"
        f"{_divider()}"
    )


def _build_queue_message() -> str:
    jobs = list_jobs()
    if not jobs:
        return (
            f"📥 <b>Content Queue</b>\n"
            f"{_divider()}\n"
            f"\n"
            f"  📭  Queue is empty.\n"
            f"\n"
            f"Use /generate &lt;topic&gt; to create new content.\n"
            f"{_divider()}"
        )
    lines = [
        f"📥 <b>Content Queue</b>  ·  {len(jobs)} job{'s' if len(jobs) != 1 else ''}",
        _divider(),
        "",
    ]
    for i, job in enumerate(jobs, 1):
        lines.append(f"  {i}. {_format_job_card(job, compact=True)}")
    lines.extend([
        "",
        f"Tap a job ID, then /approve or /publish.",
        _divider(),
    ])
    return "\n".join(lines)


def _build_trends_message() -> str:
    items = list_trends()
    if not items:
        return (
            f"🔥 <b>Trend Radar</b>\n"
            f"{_divider()}\n"
            f"\n"
            f"  📡  No trend candidates yet.\n"
            f"\n"
            f"Add trends via the API or Telegram.\n"
            f"{_divider()}"
        )
    lines = [
        f"🔥 <b>Trend Radar</b>  ·  {len(items)} candidate{'s' if len(items) != 1 else ''}",
        _divider(),
        "",
    ]
    for item in items:
        risk = "🟢" if item.risk_score < 30 else ("🟡" if item.risk_score < 60 else "🔴")
        lines.append(
            f"  {risk} {_score_bar(item.total_score, 8)}\n"
            f"      {html.escape(item.topic)}\n"
            f"      <i>src: {html.escape(item.source)}</i>"
        )
        lines.append("")
    lines.append(_divider())
    return "\n".join(lines)


def _build_analytics_message() -> str:
    items = analytics_summary()
    if not items:
        return (
            f"📈 <b>Analytics Dashboard</b>\n"
            f"{_divider()}\n"
            f"\n"
            f"  📭  No analytics snapshots yet.\n"
            f"\n"
            f"Data appears after content is published.\n"
            f"{_divider()}"
        )
    lines = [
        f"📈 <b>Analytics Dashboard</b>  ·  {len(items)} snapshot{'s' if len(items) != 1 else ''}",
        _divider(),
        "",
    ]
    for item in items:
        platform_icon = "🎬" if item["platform"] == "youtube" else "📸"
        eng = float(item["engagement_rate"])
        eng_icon = "🔥" if eng > 5 else ("📊" if eng > 1 else "📉")
        lines.append(
            f"  {platform_icon} <b>{html.escape(str(item['platform']).title())}</b>\n"
            f"      👁️ {item['views']:,} views  ·  ❤️ {item['likes']:,}  ·  💬 {item['comments']:,}\n"
            f"      {eng_icon} Engagement: <b>{item['engagement_rate']}%</b>  ·  ⏱️ {item['watch_seconds']:,}s watched"
        )
        lines.append("")
    lines.append(_divider())
    return "\n".join(lines)


def _build_help_message() -> str:
    return (
        f"📚 <b>Command Reference</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"<b>🎬 Content creation</b>\n"
        f"  /generate &lt;topic&gt; — Create a Hindi preview\n"
        f"  /approve &lt;job-id&gt; — Lock content for publishing\n"
        f"  /publish &lt;job-id&gt; — Publish approved content\n"
        f"\n"
        f"<b>📊 Monitoring</b>\n"
        f"  /status — System health dashboard\n"
        f"  /queue — Review pending content\n"
        f"  /trends — Browse trend candidates\n"
        f"  /analytics — Performance metrics\n"
        f"\n"
        f"<b>⚙️ Controls</b>\n"
        f"  /pause — Pause all automation\n"
        f"  /resume — Resume automation\n"
        f"  /menu — Show main menu\n"
        f"\n"
        f"<b>💡 Tips</b>\n"
        f"  • All content requires approval before publishing\n"
        f"  • Use short job IDs (first 8 chars) with commands\n"
        f"  • The factory starts paused for safety\n"
        f"\n"
        f"{_divider()}"
    )


def _build_controls_message() -> str:
    return (
        f"⚙️ <b>Controls</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"<b>Create content</b>\n"
        f"  /generate &lt;topic&gt;\n"
        f"\n"
        f"<b>Review &amp; approve</b>\n"
        f"  /approve &lt;job-id&gt;  — after careful review\n"
        f"  /publish &lt;job-id&gt;  — safe publisher mode\n"
        f"\n"
        f"<b>Factory control</b>\n"
        f"  Use the buttons below to pause/resume.\n"
        f"\n"
        f"{_divider()}"
    )


# ── Main update handler ────────────────────────────────────────────────
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

        # ── Callback query handling ─────────────────────────────────
        if callback:
            await answer_callback(str(callback.get("id", "")))
            data = callback.get("data", "")

            if data == "pause":
                set_paused(True)
                await send_message(
                    chat_id,
                    f"⏸️ <b>Factory paused</b>\n{_divider()}\n\nAll publishing is suspended.\nApproval-gated jobs remain in queue.\n\n{_divider()}",
                    _keyboard(),
                )

            elif data == "resume":
                set_paused(False)
                await send_message(
                    chat_id,
                    f"▶️ <b>Factory resumed</b>\n{_divider()}\n\nPublishing is now active.\nAll content still requires approval.\n\n{_divider()}",
                    _keyboard(),
                )

            elif data in {"create", "controls"}:
                await send_message(chat_id, _build_controls_message(), _keyboard())

            elif data in {"help", "menu"}:
                if data == "menu":
                    await send_message(chat_id, _build_start_message(), _keyboard())
                else:
                    await send_message(chat_id, _build_help_message(), _keyboard())

            elif data == "status":
                await send_message(chat_id, _build_status_message(), _keyboard())

            elif data == "queue":
                await send_message(chat_id, _build_queue_message(), _keyboard())

            elif data == "trends":
                await send_message(chat_id, _build_trends_message(), _keyboard())

            elif data == "analytics":
                await send_message(chat_id, _build_analytics_message(), _keyboard())

            elif data == "youtube_auth":
                status = connection_status()
                if status["authorized"]:
                    await send_message(
                        chat_id,
                        "✅ <b>YouTube is connected.</b>\nUpload remains disabled until you explicitly enable it.",
                        _back_keyboard(),
                    )
                else:
                    try:
                        url = create_authorization_url()
                        await send_message(
                            chat_id,
                            f"🔗 <b>Connect YouTube</b>\n\nOpen this private link:\n{html.escape(url)}\n\nIt expires in ten minutes.",
                            _back_keyboard(),
                        )
                    except RuntimeError as exc:
                        await send_message(chat_id, f"⚠️ YouTube setup is incomplete: {html.escape(str(exc))}", _back_keyboard())

            elif data == "refresh":
                await send_message(chat_id, _build_status_message(), _keyboard())

            elif data.startswith("approve:"):
                job_id = data.split(":", 1)[1]
                job = approve_job(job_id, user_id)
                await send_message(
                    chat_id,
                    (
                        f"✅ <b>Job approved</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        f"Content version v{job.content_version} is now locked.\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job.id),
                )

            elif data.startswith("reject:"):
                job_id = data.split(":", 1)[1]
                await send_message(
                    chat_id,
                    (
                        f"❌ <b>Job flagged for revision</b>\n{_divider()}\n\n"
                        f"Job <code>{html.escape(job_id[:8])}</code> was not approved.\n"
                        f"Re-generate or update content before approving.\n\n"
                        f"{_divider()}"
                    ),
                    _back_keyboard(),
                )

            elif data.startswith("publish:"):
                job_id = data.split(":", 1)[1]
                job = publish_job(job_id)
                mock_label = " (mock)" if get_settings().mock_publishing else ""
                await send_message(
                    chat_id,
                    (
                        f"📤 <b>Published{mock_label}</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        f"{_divider()}"
                    ),
                    _keyboard(),
                )

            elif data.startswith("quality:"):
                job_id = data.split(":", 1)[1]
                from .quality import validate_content
                from .db import SessionLocal, ContentJob

                with SessionLocal() as session:
                    job = session.get(ContentJob, job_id)
                    if job is None:
                        await send_message(chat_id, "⚠️ Job not found.", _back_keyboard())
                        return
                    report = validate_content(
                        topic=job.topic,
                        script=job.script_text,
                        metadata_json=job.metadata_json,
                        render_path=job.render_path,
                    )
                gate_icon = "✅" if report.passed else "🚫"
                blockers_text = "\n".join(f"    ⚠️ {b}" for b in report.blockers) if report.blockers else "    None"
                await send_message(
                    chat_id,
                    (
                        f"🔍 <b>Quality Report</b>\n{_divider()}\n\n"
                        f"  {gate_icon}  Gate: <b>{'PASSED' if report.passed else 'BLOCKED'}</b>\n"
                        f"  📊  Score: <b>{report.score}/100</b>\n\n"
                        f"  <b>Blockers:</b>\n{blockers_text}\n\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job_id),
                )

            return

        # ── Text command handling ───────────────────────────────────
        text = (message.get("text") or "").strip()

        if text in {"/start", "/menu"}:
            await send_message(chat_id, _build_start_message(), _keyboard())

        elif text == "/help":
            await send_message(chat_id, _build_help_message(), _keyboard())

        elif text == "/status":
            await send_message(chat_id, _build_status_message(), _keyboard())

        elif text == "/queue":
            await send_message(chat_id, _build_queue_message(), _keyboard())

        elif text == "/trends":
            await send_message(chat_id, _build_trends_message(), _keyboard())

        elif text == "/analytics":
            await send_message(chat_id, _build_analytics_message(), _keyboard())

        elif text.startswith("/generate "):
            topic = text[10:].strip()
            if not topic:
                await send_message(chat_id, "⚠️ Please provide a topic: /generate &lt;topic&gt;", _keyboard())
                return
            job = generate_content(create_job(topic, "both").id)
            await send_message(
                chat_id,
                (
                    f"✨ <b>Preview created</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"⚠️ Review the content carefully before approval.\n"
                    f"{_divider()}"
                ),
                _job_keyboard(job.id),
            )

        elif text.startswith("/approve "):
            job_id = text[9:].strip()
            if not job_id:
                await send_message(chat_id, "⚠️ Usage: /approve &lt;job-id&gt;", _keyboard())
                return
            job = approve_job(job_id, user_id)
            await send_message(
                chat_id,
                (
                    f"✅ <b>Job approved</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"Gated until the factory is resumed.\n"
                    f"{_divider()}"
                ),
                _job_keyboard(job.id),
            )

        elif text.startswith("/publish "):
            job_id = text[9:].strip()
            if not job_id:
                await send_message(chat_id, "⚠️ Usage: /publish &lt;job-id&gt;", _keyboard())
                return
            job = publish_job(job_id)
            mock_label = " (mock)" if get_settings().mock_publishing else ""
            await send_message(
                chat_id,
                (
                    f"📤 <b>Published{mock_label}</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"{_divider()}"
                ),
                _keyboard(),
            )

        elif text == "/pause":
            set_paused(True)
            await send_message(
                chat_id,
                f"⏸️ <b>Factory paused</b>\n{_divider()}\n\nAll publishing is now suspended.\n\n{_divider()}",
                _keyboard(),
            )

        elif text == "/resume":
            set_paused(False)
            await send_message(
                chat_id,
                f"▶️ <b>Factory resumed</b>\n{_divider()}\n\nPublishing active. Approval gates remain enforced.\n\n{_divider()}",
                _keyboard(),
            )

        else:
            await send_message(
                chat_id,
                "🤔 Command not recognised.\n\nUse /help to see available commands or /menu to open the control panel.",
                _keyboard(),
            )

    except Exception as exc:
        logger.exception("Telegram update failed")
        safe_msg = _safe_error_text(exc)
        await send_message(
            chat_id,
            (
                f"⚠️ <b>Action not completed</b>\n{_divider()}\n\n"
                f"{safe_msg}\n\n"
                f"No publication was triggered.\n"
                f"{_divider()}"
            ),
            _keyboard(),
        )
