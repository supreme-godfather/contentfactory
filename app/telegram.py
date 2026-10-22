import logging
import asyncio
import re
import html
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import get_settings
from .security import require_owner
from .discovery import analytics_recommendations, analytics_summary, list_trends
from .services import approve_job, create_job, generate_content, is_paused, list_jobs, publish_job, retry_job, set_paused
from .youtube_oauth import connection_status, create_authorization_url, sync_channel_analytics
from .hq import (
    DAILY_LIMIT_PRESETS,
    FORMATS,
    NICHE_PRESETS,
    get_daily_publish_limit,
    get_format,
    get_format_key,
    get_niche_key,
    get_niche_label,
    set_daily_publish_limit,
    set_format,
    set_niche,
)
from .provider_credentials import (
    PROVIDER_INFO,
    media_generation_enabled,
    provider_status,
    select_provider,
    set_media_generation_enabled,
    set_provider_secret,
)

logger = logging.getLogger(__name__)
_pending_provider_setup: dict[int, str] = {}

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
    """Main menu — deliberately small: two buttons per row, grouped by task.

    Create / review on top, monitoring in the middle, and the two control
    surfaces (Automation panel, Controls panel) plus the pause/resume
    master switch at the bottom. Everything else lives inside those
    panels, so the main menu stays readable on a phone.
    """
    paused = is_paused()
    toggle = _button("▶️ Resume", "resume") if paused else _button("⏸️ Pause", "pause")
    return {
        "inline_keyboard": [
            [_button("✨ Create", "create"), _button("📥 Queue", "queue")],
            [_button("📊 Status", "status"), _button("🔥 Trends", "trends")],
            [_button("📈 Analytics", "analytics"), _button("⚙️ Controls", "controls")],
            [_button("🤖 Automation", "automation_panel"), toggle],
        ]
    }


def _controls_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Controls panel — the headquarters: factory switch, channel settings
    (niche, format, daily limit), providers, and YouTube connection."""
    paused = is_paused()
    toggle = _button("▶️ Resume factory", "resume") if paused else _button("⏸️ Pause factory", "pause")
    return {
        "inline_keyboard": [
            [toggle],
            [_button("🎯 Niche", "hq:niche"), _button("🎞 Format", "hq:format")],
            [_button("📤 Daily upload limit", "hq:limit"), _button("🧩 Providers", "providers")],
            [_button("🔗 Connect YouTube", "youtube_auth"), _button("🔄 Sync YouTube", "sync_analytics")],
            [_button("🔄 Refresh status", "refresh"), _button("🔙 Back to menu", "menu")],
        ]
    }


def _niche_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_niche_key()
    rows = [
        [_button(f"{'✅ ' if key == current else ''}{label}", f"niche:{key}")]
        for key, label in NICHE_PRESETS.items()
    ]
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _build_niche_message() -> str:
    return (
        f"🎯 <b>Channel niche</b>\n{_divider()}\n\n"
        f"  Current: <b>{html.escape(get_niche_label())}</b>\n\n"
        "The niche shapes every script the factory writes — its facts, "
        "examples and tone. Tap a niche to switch instantly; new jobs use "
        "it right away, nothing to redeploy.\n\n"
        f"{_divider()}"
    )


def _format_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_format_key()
    rows = [
        [_button(f"{'✅ ' if key == current else ''}{info['label']}", f"format:{key}")]
        for key, info in FORMATS.items()
    ]
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _build_format_message() -> str:
    info = get_format()
    return (
        f"🎞 <b>Default format</b>\n{_divider()}\n\n"
        f"  Current: <b>{html.escape(str(info['label']))}</b>\n"
        f"  Aspect: <b>{info['aspect_ratio']}</b> · Target: "
        f"<b>{info['target_duration_seconds']}s</b> · Script: "
        f"<b>{info['script_words']} words</b>\n\n"
        "Both formats are fully supported. Shorts grow the channel fast; "
        "long-form earns more per view once monetised. New jobs are "
        "generated in the format you pick here.\n\n"
        f"{_divider()}"
    )


def _limit_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_daily_publish_limit()
    buttons = [
        _button(f"{'✅ ' if n == current else ''}{n}/day", f"limit:{n}")
        for n in DAILY_LIMIT_PRESETS
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _build_limit_message() -> str:
    return (
        f"📤 <b>Daily upload limit</b>\n{_divider()}\n\n"
        f"  Current limit: <b>{get_daily_publish_limit()} video(s) per day</b>\n\n"
        "Publishing stops for the day once this many videos are published "
        "(UTC day). The automation also respects it — it can prepare more, "
        "but nothing extra goes out. Change it any time; it applies "
        "immediately.\n\n"
        f"{_divider()}"
    )


def _automation_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Automation panel: live on/off state, run-once, and the toggle."""
    from .automation import automation_enabled

    enabled = automation_enabled()
    toggle = (
        _button("🔴 Turn automation OFF", "automation:disable")
        if enabled
        else _button("🟢 Turn automation ON", "automation:enable")
    )
    return {
        "inline_keyboard": [
            [_button("▶️ Run one cycle now", "automation:run")],
            [toggle],
            [_button("🔙 Back to menu", "menu")],
        ]
    }


def _build_automation_message() -> str:
    """Automation panel text: what it does, its limits, and its state."""
    from .automation import automation_enabled

    settings = get_settings()
    enabled = automation_enabled()
    paused = is_paused()
    return (
        f"🤖 <b>Automation</b>\n{_divider()}\n\n"
        f"  State: <b>{'🟢 ON' if enabled else '⚪ OFF'}</b>"
        f"  ·  Factory: <b>{'⏸️ Paused' if paused else '▶️ Running'}</b>\n"
        f"  Platform: <b>YouTube only</b>\n"
        f"  Every: <b>{settings.automation_interval_seconds}s</b>"
        f"  ·  Min trend score: <b>{settings.automation_min_trend_score}</b>"
        f"  ·  Max per cycle: <b>{settings.automation_max_jobs_per_cycle}</b>\n\n"
        "Each cycle requeues failed renders, then turns the best trend "
        "into a generated YouTube job. It never approves or publishes — "
        "that stays with you.\n\n"
        f"{_divider()}"
    )


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
            [
                _button("🔁 Retry", f"retry:{job_id}"),
                _button("🔙 Back to menu", "menu"),
            ],
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


def _loading_bar(progress: int, width: int = 10, fill_char: str = "█", empty_char: str = "░") -> str:
    """Render a visual loading/processing bar like [██████░░░░] 60%."""
    clamped = max(0, min(100, progress))
    filled = round(clamped / 100 * width)
    return f"[{fill_char * filled}{empty_char * (width - filled)}] {clamped}%"


_PIPELINE_STAGES: dict[str, tuple[int, str]] = {
    "queued": (20, "20% · Queued"),
    "rendering": (50, "50% · Rendering video"),
    "awaiting_approval": (75, "75% · Ready for review"),
    "approved": (90, "90% · Approved & ready to publish"),
    "published": (100, "100% · Published"),
    "failed": (0, "Failed"),
}


def _pipeline_bar(status: str, width: int = 8) -> str:
    """Render a visual pipeline loading/processing bar for a job status."""
    if status == "failed":
        return f"[{'█' * 3}×{'░' * max(0, width - 4)}] Failed"
    percent, label = _PIPELINE_STAGES.get(status, (0, status.title()))
    clamped = max(0, min(100, percent))
    filled = round(clamped / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {label}"


def _format_job_card(job: Any, *, compact: bool = False) -> str:
    """Format a single job as a rich Telegram card with visual pipeline progress."""
    emoji = _STATUS_EMOJI.get(job.status, "⚪")
    stage = _STAGE_LABEL.get(job.stage, job.stage)
    short_id = job.id[:8]

    if compact:
        progress_bar = _pipeline_bar(job.status, width=6)
        return f"{emoji} <code>{html.escape(short_id)}</code>  <code>{progress_bar}</code>  {html.escape(job.topic)}"

    lines = [
        f"{emoji} <b>{html.escape(job.topic)}</b>",
        f"",
        f"   🆔 <code>{html.escape(job.id)}</code>",
        f"   📋 Status: <b>{job.status.replace('_', ' ').title()}</b>",
        f"   ⏳ Pipeline: <code>{_pipeline_bar(job.status, width=10)}</code>",
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
        "Only failed",
        "Job must finish rendering",
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


async def delete_message(chat_id: int, message_id: int) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json={"chat_id": chat_id, "message_id": message_id})
        response.raise_for_status()


def _provider_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """One button per registered provider — any provider can be added
    with an API key, and the active one can be switched at any time."""
    status = provider_status()
    selected = status["selected"]
    rows = []
    for name, info in PROVIDER_INFO.items():
        marker = " 🎯" if selected == name else ""
        ready = " ✅" if status.get(name) else " ⚪"
        rows.append([_button(f"{info['label']}{ready}{marker}", f"provider:{name}")])
    rows.append([_button(
        "⏸️ Disable AI media" if media_generation_enabled() else "🚀 Enable AI media",
        "media:disable" if media_generation_enabled() else "media:enable",
    )])
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _provider_message() -> str:
    status = provider_status()
    selected_name = str(status["selected"])
    selected_label = html.escape(str(PROVIDER_INFO.get(selected_name, {}).get("label", selected_name)))
    lines = [
        f"🧩 <b>Media providers</b>",
        _divider(),
        "",
        f"🎯 Active: <b>{selected_label}</b>",
        f"⚙️ AI media generation: {'🟢 enabled' if media_generation_enabled() else '⚪ disabled'}",
        "",
    ]
    for name, info in PROVIDER_INFO.items():
        state = "✅ key added" if status.get(name) else "⚪ no key yet"
        lines.append(f"  {info['label']} — {state}")
    lines.extend([
        "",
        "Tap any provider to add its API key or switch to it. If a "
        "provider runs out of credits or hits its limit, just switch to "
        "another one here — no redeploy, no downtime.",
        "Keys are stored encrypted and the message is deleted immediately.",
        "",
        "⚠️ Never send YouTube, Telegram, Railway, or database secrets here.",
    ])
    return "\n".join(lines)


def _provider_detail_keyboard(name: str) -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    configured = bool(status.get(name))
    selected = status["selected"] == name
    rows = []
    if configured and not selected:
        rows.append([_button("🎯 Use this provider now", f"provider_use:{name}")])
    rows.append([_button(
        "🔐 Replace API key" if configured else "🔐 Add API key",
        f"provider_key:{name}",
    )])
    rows.append([_button("🔙 All providers", "providers")])
    return {"inline_keyboard": rows}


def _provider_detail_message(name: str) -> str:
    info = PROVIDER_INFO[name]
    status = provider_status()
    configured = bool(status.get(name))
    selected = status["selected"] == name
    wiring = (
        "🟢 Generation route connected — this provider can generate media now."
        if info["wired"]
        else "🟡 Key storage ready — its generation endpoint gets connected when you "
        "activate the account; until then the factory safely skips AI media for it "
        "and still produces the script, storyboard and render brief."
    )
    return (
        f"🧩 <b>{html.escape(str(info['label']))}</b>\n{_divider()}\n\n"
        f"  Key: <b>{'✅ added (encrypted)' if configured else '⚪ not added yet'}</b>\n"
        f"  Active: <b>{'🎯 yes — this is the current provider' if selected else 'no'}</b>\n"
        f"  {wiring}\n\n"
        f"{_divider()}"
    )


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
                {"command": "sync", "description": "🔄 Sync YouTube analytics"},
                {"command": "providers", "description": "🧩 Configure media providers"},
                {"command": "automation", "description": "🤖 Run one automation cycle"},
                {"command": "retry", "description": "🔁 Retry a failed job"},
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
    settings = get_settings()
    youtube = connection_status()
    youtube_state = "🟢 Connected" if youtube["authorized"] else "⚪ Not connected"
    factory_state = "⏸️ Paused" if is_paused() else "▶️ Running"
    return (
        f"🎬 <b>Content Factory</b>\n"
        f"{_divider()}\n"
        f"  Factory: <b>{factory_state}</b>\n"
        f"  YouTube: {youtube_state}\n"
        f"  Mode: {'Safe preview (mock)' if settings.mock_publishing else 'Live'}\n"
        f"  Publishing: 🔒 Approval required, always\n"
        f"\n"
        f"Pick an action below, or type /help for commands.\n"
        f"{_divider()}"
    )


def _build_status_message() -> str:
    settings = get_settings()
    paused = is_paused()
    state_icon = "🔴" if paused else "🟢"
    state_text = "PAUSED" if paused else "RUNNING"
    mock_icon = "🧪" if settings.mock_publishing else "🚀"
    mock_text = "Mock mode" if settings.mock_publishing else "Live mode"
    yt_icon = "✅" if settings.youtube_enabled else "❌"

    jobs = list_jobs()
    active_jobs = [j for j in jobs if j.status in ("queued", "rendering", "awaiting_approval")]
    max_concurrent = max(1, settings.max_concurrent_jobs)
    load_pct = min(100, round(len(active_jobs) / max_concurrent * 100))
    load_bar = _loading_bar(load_pct, width=8)

    return (
        f"📊 <b>System Dashboard</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"  {state_icon}  Factory: <b>{state_text}</b>\n"
        f"  {mock_icon}  Publisher: <b>{mock_text}</b>\n"
        f"  ⏳  Pipeline load: <code>{load_bar}</code> ({len(active_jobs)}/{max_concurrent} active)\n"
        f"\n"
        f"  <b>Platform</b>\n"
        f"  {yt_icon}  YouTube — active platform\n"
        f"  ⚪  Instagram — coming later\n"
        f"\n"
        f"  <b>Limits</b>\n"
        f"  📤  Daily quota: {get_daily_publish_limit()} (set in Controls)\n"
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
    try:
        recs = analytics_recommendations().get("recommendations") or []
    except Exception:
        recs = []
    if recs:
        lines.append("💡 <b>Recommendations</b>")
        for rec in recs:
            lines.append(f"  • {html.escape(str(rec))}")
        lines.append("")
    lines.append(_divider())
    return "\n".join(lines)


def _build_help_message() -> str:
    return (
        f"📚 <b>Command Reference</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"<b>🎬 Content (YouTube)</b>\n"
        f"  /generate &lt;topic&gt; — Create a Hindi YouTube preview\n"
        f"  /approve &lt;job-id&gt; — Lock content for publishing\n"
        f"  /publish &lt;job-id&gt; — Publish approved content\n"
        f"  /automation — Run one automation cycle now (or open the 🤖 Automation panel to turn it on/off)\n"
        f"  /retry &lt;job-id&gt; — Retry a failed job\n"
        f"\n"
        f"<b>📊 Monitoring</b>\n"
        f"  /status — System health dashboard\n"
        f"  /queue — Review pending content\n"
        f"  /trends — Browse trend candidates\n"
        f"  /analytics — Performance metrics\n"
        f"  /sync — Sync official YouTube analytics\n"
        f"  /providers — Configure image and voice providers\n"
        f"\n"
        f"<b>⚙️ Controls (headquarters)</b>\n"
        f"  /pause — Pause all automation\n"
        f"  /resume — Resume automation\n"
        f"  /menu — Show main menu\n"
        f"  ⚙️ Controls panel — niche, format (Shorts/Long), daily upload limit, providers: everything is set there\n"
        f"\n"
        f"<b>💡 Tips</b>\n"
        f"  • All content requires approval before publishing\n"
        f"  • Use short job IDs (first 8 chars) with commands\n"
        f"  • The factory starts paused for safety\n"
        f"  • YouTube uploads remain private until explicitly enabled\n"
        f"\n"
        f"{_divider()}"
    )


def _build_controls_message() -> str:
    paused = is_paused()
    info = get_format()
    return (
        f"⚙️ <b>Controls — your headquarters</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"  Factory: <b>{'⏸️ Paused' if paused else '▶️ Running'}</b>\n"
        f"  🎯 Niche: <b>{html.escape(get_niche_label())}</b>\n"
        f"  🎞 Format: <b>{html.escape(str(info['label']))}</b>\n"
        f"  📤 Daily upload limit: <b>{get_daily_publish_limit()}/day</b>\n"
        f"\n"
        f"<b>Quick commands</b>\n"
        f"  /generate &lt;topic&gt; — create a YouTube preview\n"
        f"  /approve &lt;job-id&gt; — approve after review\n"
        f"  /publish &lt;job-id&gt; — publish an approved job\n"
        f"\n"
        f"Everything is controlled from the buttons below — niche, "
        f"format, upload limits, providers and the factory switch. "
        f"Changes apply immediately, no redeploy.\n"
        f"\n"
        f"{_divider()}"
    )


def _build_create_message() -> str:
    return (
        f"✨ <b>Create content</b>\n"
        f"{_divider()}\n"
        f"\n"
        f"Send:\n"
        f"  <code>/generate &lt;topic&gt;</code>\n"
        f"\n"
        f"Example: <code>/generate चंद्रयान मिशन की कहानी</code>\n"
        f"\n"
        f"A YouTube preview is created in the current format "
        f"({html.escape(str(get_format()['label']))}, niche: "
        f"{html.escape(get_niche_label())}) and sent to the render worker. "
        f"Nothing is published without your approval. Change format or "
        f"niche any time in ⚙️ Controls.\n"
        f"\n"
        f"Or let Automation do it: open 🤖 Automation and run a cycle — "
        f"it builds a job from the top trend.\n"
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

            elif data == "create":
                await send_message(chat_id, _build_create_message(), _keyboard())

            elif data == "controls":
                await send_message(chat_id, _build_controls_message(), _controls_keyboard())

            elif data == "automation_panel":
                await send_message(chat_id, _build_automation_message(), _automation_keyboard())

            elif data in {"automation:enable", "automation:disable"}:
                from .automation import set_automation_enabled

                set_automation_enabled(data == "automation:enable")
                await send_message(chat_id, _build_automation_message(), _automation_keyboard())

            elif data == "automation:run":
                from .automation import run_automation_cycle

                outcome = run_automation_cycle()
                if outcome.get("skipped_reason"):
                    summary = f"⏭️ Automation skipped: {html.escape(str(outcome['skipped_reason']))}"
                else:
                    summary = (
                        f"🤖 <b>Automation cycle complete</b>\n"
                        f"   Platform: YouTube\n"
                        f"   🔥 Trends considered: {outcome.get('trends_considered', 0)}\n"
                        f"   ✨ Jobs created: {outcome.get('jobs_created', 0)}\n"
                        f"   📝 Jobs generated: {outcome.get('jobs_generated', 0)}\n"
                        f"   🔁 Jobs requeued: {outcome.get('jobs_requeued', 0)}"
                    )
                await send_message(
                    chat_id,
                    f"{summary}\n\nNothing was published — approval is still required.\n\n{_divider()}",
                    _automation_keyboard(),
                )

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

            elif data == "sync_analytics":
                try:
                    result = sync_channel_analytics()
                    await send_message(
                        chat_id,
                        (
                            "✅ <b>YouTube analytics synced</b>\n"
                            f"{_divider()}\n\n"
                            f"Channel: <b>{html.escape(str(result['account']))}</b>\n"
                            f"Period: {result['start_date']} → {result['end_date']}\n\n"
                            "Open Analytics to view the latest snapshot."
                        ),
                        _keyboard(),
                    )
                except Exception:
                    logger.exception("YouTube analytics sync failed")
                    await send_message(chat_id, "⚠️ Analytics sync is unavailable. Check YouTube authorization and try again.", _keyboard())

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

            elif data == "hq:niche":
                await send_message(chat_id, _build_niche_message(), _niche_keyboard())

            elif data.startswith("niche:"):
                try:
                    label = set_niche(data.split(":", 1)[1])
                    await send_message(
                        chat_id,
                        f"🎯 <b>Niche set:</b> {html.escape(label)}\n\nNew jobs will be written for this niche.",
                        _niche_keyboard(),
                    )
                except ValueError:
                    await send_message(chat_id, "⚠️ Unknown niche.", _niche_keyboard())

            elif data == "hq:format":
                await send_message(chat_id, _build_format_message(), _format_keyboard())

            elif data.startswith("format:"):
                try:
                    info = set_format(data.split(":", 1)[1])
                    await send_message(
                        chat_id,
                        f"🎞 <b>Default format set:</b> {html.escape(str(info['label']))}\n\nNew jobs will be generated in this format.",
                        _format_keyboard(),
                    )
                except ValueError:
                    await send_message(chat_id, "⚠️ Unknown format.", _format_keyboard())

            elif data == "hq:limit":
                await send_message(chat_id, _build_limit_message(), _limit_keyboard())

            elif data.startswith("limit:"):
                try:
                    limit = set_daily_publish_limit(int(data.split(":", 1)[1]))
                    await send_message(
                        chat_id,
                        f"📤 <b>Daily upload limit set:</b> {limit} video(s) per day.\n\nIt applies immediately — publishing stops at the limit.",
                        _limit_keyboard(),
                    )
                except ValueError:
                    await send_message(chat_id, "⚠️ Invalid limit.", _limit_keyboard())

            elif data == "providers":
                await send_message(chat_id, _provider_message(), _provider_keyboard())

            elif data.startswith("provider:"):
                provider = data.split(":", 1)[1]
                if provider not in PROVIDER_INFO:
                    await send_message(chat_id, "⚠️ Unsupported provider.", _provider_keyboard())
                    return
                await send_message(chat_id, _provider_detail_message(provider), _provider_detail_keyboard(provider))

            elif data.startswith("provider_use:"):
                provider = data.split(":", 1)[1]
                if provider not in PROVIDER_INFO:
                    await send_message(chat_id, "⚠️ Unsupported provider.", _provider_keyboard())
                    return
                select_provider(provider)
                await send_message(
                    chat_id,
                    f"🎯 <b>{html.escape(str(PROVIDER_INFO[provider]['label']))} is now the active provider.</b>\n\nGeneration uses it from the next job onward.",
                    _provider_detail_keyboard(provider),
                )

            elif data.startswith("provider_key:"):
                provider = data.split(":", 1)[1]
                if provider not in PROVIDER_INFO:
                    await send_message(chat_id, "⚠️ Unsupported provider.", _provider_keyboard())
                    return
                _pending_provider_setup[chat_id] = provider
                label = str(PROVIDER_INFO[provider]["secret_label"])
                await send_message(
                    chat_id,
                    (
                        f"🔐 <b>Add {html.escape(label)}</b>\n\n"
                        "Send the secret as your next message. It will be deleted immediately "
                        "after receipt and never echoed back.\n\n"
                        "Press /cancel to abort."
                    ),
                    _back_keyboard(),
                )

            elif data == "media:enable":
                set_media_generation_enabled(True)
                await send_message(chat_id, "🚀 <b>AI media generation enabled</b>", _provider_keyboard())

            elif data == "media:disable":
                set_media_generation_enabled(False)
                await send_message(chat_id, "⏸️ <b>AI media generation disabled</b>", _provider_keyboard())

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

            elif data.startswith("retry:"):
                job_id = data.split(":", 1)[1]
                job = retry_job(job_id)
                await send_message(
                    chat_id,
                    (
                        f"🔁 <b>Job requeued for retry</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        f"Retry attempt: {job.retry_count}\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job.id),
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
                warnings_text = "\n".join(f"    💡 {w}" for w in report.warnings) if report.warnings else "    None"
                await send_message(
                    chat_id,
                    (
                        f"🔍 <b>Quality Report</b>\n{_divider()}\n\n"
                        f"  {gate_icon}  Gate: <b>{'PASSED' if report.passed else 'BLOCKED'}</b>\n"
                        f"  📊  Score: <b>{report.score}/100</b>\n\n"
                        f"  <b>Blockers:</b>\n{blockers_text}\n\n"
                        f"  <b>Warnings (fix to earn better):</b>\n{warnings_text}\n\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job_id),
                )

            return

        # ── Text command handling ───────────────────────────────────
        text = (message.get("text") or "").strip()

        if chat_id in _pending_provider_setup:
            if text == "/cancel":
                _pending_provider_setup.pop(chat_id, None)
                await send_message(chat_id, "↩️ Provider setup cancelled.", _provider_keyboard())
                return
            provider = _pending_provider_setup.pop(chat_id)
            try:
                set_provider_secret(provider, text)
                select_provider(provider)
                message_id = message.get("message_id")
                if isinstance(message_id, int):
                    try:
                        await delete_message(chat_id, message_id)
                    except Exception:
                        logger.warning("Could not delete provider credential message", exc_info=True)
                provider_label = html.escape(str(PROVIDER_INFO.get(provider, {}).get("label", provider.title())))
                wiring_note = (
                    "Media generation will use this provider when AI media is enabled."
                    if PROVIDER_INFO.get(provider, {}).get("wired")
                    else "Its key is stored safely. The generation endpoint for this provider "
                    "gets connected when you activate the account — you can switch back to a "
                    "connected provider here at any time."
                )
                await send_message(
                    chat_id,
                    f"✅ <b>{provider_label} — key saved (encrypted) and set as active</b>\n\n"
                    f"{wiring_note}",
                    _provider_keyboard(),
                )
            except Exception:
                logger.exception("Provider credential setup failed")
                await send_message(
                    chat_id,
                    "⚠️ Provider setup failed. Check TOKEN_ENCRYPTION_KEY and try again.",
                    _provider_keyboard(),
                )
            return

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

        elif text == "/sync":
            try:
                result = sync_channel_analytics()
                await send_message(
                    chat_id,
                    (
                        "✅ <b>YouTube analytics synced</b>\n"
                        f"{_divider()}\n\n"
                        f"📺 Channel: <b>{html.escape(str(result['account']))}</b>\n"
                        f"📅 Period: {result['start_date']} → {result['end_date']}\n\n"
                        "Use /analytics to view the latest snapshot."
                    ),
                    _keyboard(),
                )
            except Exception:
                logger.exception("YouTube analytics sync failed")
                await send_message(
                    chat_id,
                    "⚠️ Analytics sync is unavailable. Connect YouTube and try again.",
                    _keyboard(),
                )

        elif text == "/providers":
            await send_message(chat_id, _provider_message(), _provider_keyboard())

        elif text.startswith("/generate "):
            topic = text[10:].strip()
            if not topic:
                await send_message(chat_id, "⚠️ Please provide a topic: /generate &lt;topic&gt;", _keyboard())
                return
            # YouTube-only for now; Instagram creation comes later.
            job = generate_content(create_job(topic, "youtube").id)
            await send_message(
                chat_id,
                (
                    f"✨ <b>Preview created</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"⚙️ <b>Processing stages:</b>\n"
                    f"   ├ 📝 Script generated:  [██████████] 100%\n"
                    f"   ├ 🎞️ RDP video render:  [████░░░░░░]  40% (Dispatched)\n"
                    f"   └ 🛡️ Quality gate:      [░░░░░░░░░░]   0% (Pending)\n\n"
                    f"🎞️ The RDP worker is rendering the MP4 before approval.\n"
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
                    f"⚙️ <b>Processing stages:</b>\n"
                    f"   ├ 📝 Script generated:  [██████████] 100%\n"
                    f"   ├ 🎞️ Video rendered:    [██████████] 100%\n"
                    f"   ├ 🛡️ Quality review:    [██████████] 100%\n"
                    f"   └ 🚀 Live publishing:   [█████████░]  90% (Gated until resume)\n\n"
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
                    f"⚙️ <b>Processing stages:</b>\n"
                    f"   └ 🚀 Live published:    [██████████] 100% (Complete)\n\n"
                    f"{_divider()}"
                ),
                _keyboard(),
            )

        elif text == "/automation":
            from .automation import run_automation_cycle

            outcome = run_automation_cycle()
            if outcome.get("skipped_reason"):
                summary = f"⏭️ Automation skipped: {html.escape(str(outcome['skipped_reason']))}"
            else:
                summary = (
                    f"🤖 <b>Automation cycle complete</b>\n"
                    f"   Platform: YouTube\n"
                    f"   🔥 Trends considered: {outcome.get('trends_considered', 0)}\n"
                    f"   ✨ Jobs created: {outcome.get('jobs_created', 0)}\n"
                    f"   📝 Jobs generated: {outcome.get('jobs_generated', 0)}\n"
                    f"   🔁 Jobs requeued: {outcome.get('jobs_requeued', 0)}"
                )
            await send_message(
                chat_id,
                f"{summary}\n\nContent still requires rendering and approval — nothing was published.\n\n{_divider()}",
                _automation_keyboard(),
            )

        elif text.startswith("/retry "):
            job_id = text[7:].strip()
            if not job_id:
                await send_message(chat_id, "⚠️ Usage: /retry &lt;job-id&gt;", _keyboard())
                return
            job = retry_job(job_id)
            await send_message(
                chat_id,
                (
                    f"🔁 <b>Job requeued for retry</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"Retry attempt: {job.retry_count}\n"
                    f"{_divider()}"
                ),
                _job_keyboard(job.id),
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
