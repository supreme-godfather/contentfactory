import logging
import asyncio
import hashlib
import re
import html
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from .config import get_settings
from .security import require_owner
from .discovery import analytics_recommendations, analytics_summary, list_trends
from .services import (
    approve_job,
    cancel_job,
    create_job,
    generate_content,
    is_paused,
    list_jobs,
    publish_job,
    revise_job,
    retry_job,
    schedule_job,
    set_paused,
    worker_status,
)
from .youtube_oauth import connection_status, create_authorization_url, sync_channel_analytics
from .hq import (
    DAILY_LIMIT_PRESETS,
    FORMATS,
    NICHE_PRESETS,
    PUBLISH_TARGETS,
    get_daily_publish_limit,
    get_format,
    get_format_key,
    get_niche_key,
    get_niche_label,
    get_publish_target,
    get_render_worker_target,
    is_mock_publishing,
    set_daily_publish_limit,
    set_format,
    set_mock_publishing,
    set_niche,
    set_publish_target,
    set_render_worker_target,
)
from .provider_credentials import (
    PROVIDER_INFO,
    PROVIDER_ROLES,
    get_provider_for_role,
    get_elevenlabs_voice_id,
    get_huggingface_image_endpoint,
    media_generation_enabled,
    no_paid_providers_enabled,
    provider_status,
    select_provider,
    set_no_paid_providers_enabled,
    set_provider_for_role,
    set_media_generation_enabled,
    set_elevenlabs_voice_id,
    set_huggingface_image_endpoint,
    set_provider_secret,
)

logger = logging.getLogger(__name__)
_pending_provider_setup: dict[int, str] = {}
_pending_elevenlabs_voice_id: set[int] = set()
_pending_huggingface_image_endpoint: set[int] = set()
_pending_platform_setup: dict[int, str] = {}
_pending_instagram_id: set[int] = set()
_pending_schedule: dict[int, str] = {}
_unauthorized_notice_times: OrderedDict[int, float] = OrderedDict()
_UNAUTHORIZED_NOTICE_INTERVAL_SECONDS = 24 * 60 * 60
_UNAUTHORIZED_NOTICE_MAX_USERS = 4096


def _claim_unauthorized_notice(user_id: int) -> bool:
    now = time.monotonic()
    while _unauthorized_notice_times:
        _, sent_at = next(iter(_unauthorized_notice_times.items()))
        if now - sent_at < _UNAUTHORIZED_NOTICE_INTERVAL_SECONDS:
            break
        _unauthorized_notice_times.popitem(last=False)

    if user_id in _unauthorized_notice_times:
        return False
    if len(_unauthorized_notice_times) >= _UNAUTHORIZED_NOTICE_MAX_USERS:
        _unauthorized_notice_times.popitem(last=False)
    _unauthorized_notice_times[user_id] = now
    return True

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


def _link_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


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
    (niche, format, daily limit), providers, Instagram, and publishing mode."""
    paused = is_paused()
    toggle = _button("▶️ Resume factory", "resume") if paused else _button("⏸️ Pause factory", "pause")
    return {
        "inline_keyboard": [
            [toggle],
            [_button("🎯 Niche", "hq:niche"), _button("🎞 Format", "hq:format")],
            [_button("📡 Publishing destinations", "hq:publish_target")],
            [_button("📸 Reel profile", "reel_profile")],
            [_button("📤 Daily upload limit", "hq:limit"), _button("🧩 Providers", "providers")],
            [_button("🧪 Mock/live publishing mode", "publishing_mode")],
            [_button("🔗 Channel accounts", "accounts")],
            [_button("📸 Instagram setup", "instagram_setup")],
            [_button("🖥 Worker & API health", "system_health")],
            [_button("🔗 Connect YouTube", "youtube_auth"), _button("🔄 Sync YouTube", "sync_analytics")],
            [_button("📖 Instagram / YouTube setup guide", "platform_guide")],
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


def _publish_target_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_publish_target()
    rows = [
        [
            _button(
                f"{'✅ ' if target == current else ''}{label}",
                f"publish_target:{target}",
            )
        ]
        for target, label in PUBLISH_TARGETS.items()
    ]
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _build_publish_target_message() -> str:
    target = get_publish_target()
    return (
        f"📡 <b>Publishing destinations</b>\n{_divider()}\n\n"
        f"Current: <b>{html.escape(PUBLISH_TARGETS[target])}</b>\n\n"
        "This applies to new manual and automated jobs. YouTube + Instagram "
        "uses the same rendered video, with platform-specific titles, captions, "
        "tags, and covers. Each platform still needs its own connected account "
        "and publishing enabled. In dual-platform mode, uploads happen one after "
        "the other; both count toward the daily publication limit.\n\n"
        "Changing this setting does not alter jobs already in the queue. "
        "Every live upload remains approval-gated.\n"
        f"{_divider()}"
    )


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


def _reel_profile_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .hq import REEL_DURATION_PRESETS, REEL_VOICE_PRESETS, get_reel_duration, get_reel_voice

    duration = get_reel_duration()
    voice = get_reel_voice()
    rows = [
        [
            _button(
                f"{'✅ ' if seconds == duration else ''}{seconds}s",
                f"reel_duration:{seconds}",
            )
            for seconds in REEL_DURATION_PRESETS
        ]
    ]
    rows.extend(
        [
            [
                _button(
                    f"{'✅ ' if item == voice else ''}{item.rsplit('-', 1)[-1]}",
                    f"reel_voice:{item}",
                )
            ]
            for item in REEL_VOICE_PRESETS
        ]
    )
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _build_reel_profile_message() -> str:
    from .hq import get_reel_duration, get_reel_voice

    return (
        f"📸 <b>Hindi Reel profile</b>\n{_divider()}\n\n"
        "Style: faceless, dark-cinematic psychology, mindset and history.\n"
        "Format: 9:16 with timed Hindi captions and a slow cinematic camera move.\n"
        f"Duration: <b>{get_reel_duration()} seconds</b>\n"
        f"Google Hindi voice: <b>{html.escape(get_reel_voice())}</b>\n\n"
        "Only use original or properly licensed audio and source-checked facts. "
        "Music tracks are not bundled; add a licensed track during editing until a licensed music source is configured."
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
    target = PUBLISH_TARGETS[get_publish_target()]
    return (
        f"🤖 <b>Automation</b>\n{_divider()}\n\n"
        f"  State: <b>{'🟢 ON' if enabled else '⚪ OFF'}</b>"
        f"  ·  Factory: <b>{'⏸️ Paused' if paused else '▶️ Running'}</b>\n"
        f"  Platform: <b>{html.escape(target)}</b>\n"
        f"  Every: <b>{settings.automation_interval_seconds}s</b>"
        f"  ·  Min trend score: <b>{settings.automation_min_trend_score}</b>"
        f"  ·  Max per cycle: <b>{settings.automation_max_jobs_per_cycle}</b>\n\n"
        "Each cycle requeues failed renders, then turns the best trend "
        "into a generated job for the selected destination. It never approves or publishes — "
        "that stays with you.\n\n"
        f"{_divider()}"
    )


def _job_keyboard(job_id: str) -> dict[str, list[list[dict[str, str]]]]:
    """Actions for a job, keeping revision and cancellation explicit."""
    return {
        "inline_keyboard": [
            [
                _button("✅ Approve", f"approve:{job_id}"),
                _button("✍️ Revise", f"revise:{job_id}"),
            ],
            [
                _button("📤 Publish now", f"publish:{job_id}"),
                _button("🔍 Quality check", f"quality:{job_id}"),
            ],
            [_button("▶️ Preview video", f"preview:{job_id}")],
            [_button("⏰ Schedule reminder", f"schedule:{job_id}")],
            [
                _button("🔁 Retry", f"retry:{job_id}"),
                _button("🗑 Cancel job", f"cancel:{job_id}"),
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
        label = "Final touch" if job.status == "published" else "Error"
        lines.append(f"   ⚠️ {label}: {html.escape(job.error_message[:180])}")
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
        "Video preview exceeds Telegram's 49 MB preview limit.",
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


async def _send_video_preview(chat_id: int, job_id: str) -> None:
    from sqlalchemy import select

    from .db import ContentJob, SessionLocal

    if not re.fullmatch(r"[0-9a-fA-F-]{8,36}", job_id):
        await send_message(
            chat_id,
            "⚠️ Use a job ID (at least its first 8 characters): <code>/preview &lt;job-id&gt;</code>",
            _back_keyboard(),
        )
        return

    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None:
            matches = list(
                session.scalars(
                    select(ContentJob)
                    .where(ContentJob.id.startswith(job_id))
                    .limit(2)
                )
            )
            if len(matches) > 1:
                await send_message(
                    chat_id,
                    "⚠️ More than one job matches that ID. Use the full job ID.",
                    _back_keyboard(),
                )
                return
            job = matches[0] if matches else None
        if job is None:
            await send_message(chat_id, "⚠️ Job not found.", _back_keyboard())
            return
        if job.status != "awaiting_approval":
            await send_message(
                chat_id,
                "⚠️ Video preview is available after rendering and before approval.",
                _back_keyboard(),
            )
            return
        resolved_job_id = job.id
        render_path = job.render_path
        topic = job.topic

    media_root = Path(get_settings().media_root).resolve()
    video_path = Path(render_path or "").resolve()
    if (
        video_path.parent != media_root
        or video_path.name != f"{resolved_job_id}.mp4"
        or not video_path.is_file()
    ):
        await send_message(
            chat_id,
            "⚠️ The rendered MP4 is missing or unavailable for preview.",
            _back_keyboard(),
        )
        return
    await send_video(chat_id, video_path, f"Review before approval · {topic[:160]}")


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


async def send_progress_message(chat_id: int, text: str) -> int | None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=_message(chat_id, text))
        response.raise_for_status()
    payload = response.json()
    message_id = payload.get("result", {}).get("message_id") if isinstance(payload, dict) else None
    if not isinstance(message_id, int):
        raise RuntimeError("Telegram did not return the progress message ID")
    return message_id


async def finish_progress_message(
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or message_id is None:
        await send_message(chat_id, text, reply_markup)
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def send_photo(chat_id: int, image_path: Path, caption: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto"
    with image_path.open("rb") as photo:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": caption[:1000]},
                files={"photo": (image_path.name, photo, "image/jpeg")},
            )
            response.raise_for_status()


async def send_video(chat_id: int, video_path: Path, caption: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    if video_path.stat().st_size > 49 * 1024 * 1024:
        raise ValueError("Video preview exceeds Telegram's 49 MB preview limit.")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendVideo"
    with video_path.open("rb") as video:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": caption[:1000]},
                files={"video": (video_path.name, video, "video/mp4")},
            )
            response.raise_for_status()


def _publish_package_summary(job: Any) -> str:
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return "Metadata package is unavailable."
    lines = ["<b>Ready-to-publish package</b>"]
    for platform in (
        ["youtube", "instagram"] if job.platform == "both" else [job.platform]
    ):
        package = metadata.get(platform) or {}
        lines.append(f"\n<b>{html.escape(platform.title())}</b>")
        title = package.get("title") or metadata.get("title")
        caption = package.get("caption") or package.get("description") or metadata.get("description")
        hashtags = package.get("hashtags") or metadata.get("hashtags") or []
        tags = package.get("tags") or metadata.get("tags") or []
        if title:
            lines.append(f"Title: {html.escape(str(title)[:200])}")
        if caption:
            lines.append(f"Caption/description: {html.escape(str(caption)[:450])}")
        if hashtags:
            lines.append(f"Hashtags: {html.escape(' '.join(map(str, hashtags))[:300])}")
        if platform == "youtube" and tags:
            lines.append(f"YouTube tags: {html.escape(', '.join(map(str, tags))[:300])}")
    return "\n".join(lines)


async def delete_message(chat_id: int, message_id: int) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json={"chat_id": chat_id, "message_id": message_id})
        response.raise_for_status()


def _provider_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    rows = [
        [_button(
            f"{label}: {PROVIDER_ROLES[role][get_provider_for_role(role)]}",
            f"provider_role:{role}",
        )]
        for role, label in (
            ("script", "📝 Script"),
            ("image", "🖼 Image"),
            ("voice", "🎙 Voice"),
        )
    ]
    rows.append([_button(
        f"🎞 Video renderer: {get_render_worker_target()}",
        "video_pipeline",
    )])
    rows.append([_button("🔐 API credentials", "provider_credentials")])
    rows.append([_button(
        "⏸️ Disable AI media" if media_generation_enabled() else "🚀 Enable AI media",
        "media:disable" if media_generation_enabled() else "media:enable",
    )])
    rows.append([_button(
        "🛑 No-paid providers: ON" if no_paid_providers_enabled() else "⚠️ No-paid providers: OFF",
        "spending:toggle",
    )])
    rows.append([_button("🔙 Back to Controls", "controls")])
    return {"inline_keyboard": rows}


def _provider_role_keyboard(role: str) -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    current = get_provider_for_role(role)
    rows = []
    for provider, label in PROVIDER_ROLES[role].items():
        ready = (
            " ✅"
            if provider in {"google", "openai", "gemini", "huggingface", "elevenlabs"} and status.get(provider)
            else " ✅"
            if provider == "edge_tts"
            else ""
        )
        if provider == "huggingface" and role == "image":
            ready = (
                " ✅"
                if status.get("huggingface") and status.get("huggingface_image_endpoint")
                else ""
            )
        if provider == "elevenlabs" and status.get("elevenlabs") and status.get("elevenlabs_voice_id"):
            ready = " ✅"
        selected = " 🎯" if current == provider else ""
        rows.append([
            _button(
                f"{label}{ready}{selected}",
                f"provider_role_set:{role}:{provider}",
            )
        ])
    if role == "image":
        rows.append([_button(
            "🔗 Set Hugging Face image endpoint"
            + (" ✅" if status.get("huggingface_image_endpoint") else " ⚪"),
            "huggingface_image_endpoint",
        )])
    if role == "voice":
        rows.append([_button(
            "🎙️ Set ElevenLabs voice ID"
            + (" ✅" if status.get("elevenlabs_voice_id") else " ⚪"),
            "elevenlabs_voice_id",
        )])
    rows.append([_button("🔐 Add / review API credentials", "provider_credentials")])
    rows.append([_button("🔙 Back to API settings", "providers")])
    return {"inline_keyboard": rows}


def _provider_role_message(role: str) -> str:
    selected = get_provider_for_role(role)
    label = PROVIDER_ROLES[role][selected]
    return (
        f"🧩 <b>{html.escape(role.title())} provider</b>\n{_divider()}\n\n"
        f"Current choice: <b>{html.escape(label)}</b>\n\n"
        "Choose a wired option below. A ✅ means its credential is present or no key is needed. "
        + (
            "No-paid mode blocks metered voice/image providers and paid fallback; images are skipped. "
            if no_paid_providers_enabled()
            else "Configured Google/OpenAI routes may incur charges; a Hugging Face GPU endpoint is billed separately. "
        )
        + (
            "The Hugging Face FLUX route creates six scene-specific images per video and is billed separately "
            "by the GPU endpoint provider; scale-to-zero is recommended. "
            if role == "image"
            else ""
        )
        + (
            "ElevenLabs uses monthly credits; check your plan and disable any paid top-ups. "
            if role == "voice"
            else ""
        )
        + "Repeatedly limited providers are temporarily skipped. "
        "If OpenAI scriptwriting is unavailable, the built-in Hindi template is used instead. "
        "Choices apply to new jobs only."
    )


def _provider_video_message() -> str:
    railway_online = any(
        worker["worker_id"] == "railway-renderer" and worker["online"]
        for worker in worker_status()
    )
    worker_status_text = "online" if railway_online else "offline"
    return (
        "🎞 <b>Video rendering</b>\n"
        f"{_divider()}\n\n"
        "The video is assembled by FFmpeg on the Railway worker; there is no "
        "video-generation API connected yet.\n\n"
        "Selected worker: <b>railway-renderer</b>\n"
        f"Railway worker status: <b>{worker_status_text}</b>\n"
        "RDP workers are not used. If the Railway worker is offline or not deployed, "
        "jobs stay queued until it reconnects.\n\n"
        "Choose vertical / landscape and duration from Controls → Format / Reel profile. "
        "AI-created scene images and narration are selected separately in API settings.\n\n"
        "The Railway worker must be deployed in this project for rendering to start."
    )


def _provider_video_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [_button("🎯 Railway renderer only ✓", "render_worker:auto")],
            [_button("🔙 Back to API settings", "providers")],
        ]
    }


def _provider_credentials_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    rows = []
    for name, info in PROVIDER_INFO.items():
        ready = " ✅" if status.get(name) else " ⚪"
        rows.append([_button(f"{info['label']}{ready}", f"provider:{name}")])
    rows.append([_button("🔙 Back to API settings", "providers")])
    return {"inline_keyboard": rows}


def _provider_credentials_message() -> str:
    status = provider_status()
    lines = ["🔐 <b>API credentials</b>", _divider(), ""]
    for name, info in PROVIDER_INFO.items():
        state = "✅ credential added" if status.get(name) else "⚪ not added"
        wired = "connected" if info["wired"] else "not connected to generation yet"
        cooldown = status.get(f"{name}_cooldown_until")
        cooldown_note = (
            f"; cooling until {html.escape(str(cooldown))}"
            if cooldown
            else ""
        )
        lines.append(f"• {info['label']}: {state}; {wired}{cooldown_note}.")
    lines.extend([
        "",
        "Gemini is connected for scriptwriting; Edge TTS and ElevenLabs are connected for narration. "
        "AI Studio free-tier availability and quotas can change; use an API project with billing disabled "
        "if you want to avoid accidental paid Gemini usage.",
        "No-paid mode blocks Vertex/OpenAI media routes and all paid-provider fallback. "
        "When disabled, configured paid providers and automatic fallback can incur charges.",
        "API credentials are encrypted and the credential message is deleted after receipt.",
    ])
    return "\n".join(lines)


def _provider_message() -> str:
    status = provider_status()
    role_lines = [
        f"  {label}: <b>{html.escape(PROVIDER_ROLES[role][get_provider_for_role(role)])}</b>"
        for role, label in (
            ("script", "📝 Script"),
            ("image", "🖼 Image"),
            ("voice", "🎙 Voice"),
        )
    ]
    return (
        f"🧩 <b>AI &amp; media settings</b>\n{_divider()}\n\n"
        + "\n".join(role_lines)
        + "\n  🎞 Video: <b>FFmpeg render worker</b>\n\n"
        f"AI media generation: <b>{'enabled' if media_generation_enabled() else 'disabled'}</b>\n"
        f"No-paid providers: <b>{'ON' if no_paid_providers_enabled() else 'OFF'}</b>\n"
        f"Default provider: <b>{html.escape(str(PROVIDER_INFO.get(str(status['selected']), {}).get('label', status['selected'])))}</b>\n\n"
        "Set script, image, and narration providers independently below. "
        "Video rendering runs on FFmpeg at the worker, not a video API. "
        "Settings apply to new jobs; credentials are managed separately."
    )


def _provider_detail_keyboard(name: str) -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    configured = bool(status.get(name))
    selected = status["selected"] == name
    rows = []
    if configured and not selected:
        rows.append([_button("Set as default for unset roles", f"provider_use:{name}")])
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
        f"  Default for roles not customized: <b>{'🎯 yes' if selected else 'no'}</b>\n"
        f"  {wiring}\n\n"
        f"{_divider()}"
    )


async def answer_callback(callback_id: str, text: str | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not callback_id:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=15) as client:
        payload: dict[str, str] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def configure_webhook() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.public_base_url:
        return
    if not settings.telegram_webhook_secret or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,256}", settings.telegram_webhook_secret
    ):
        logger.error("Telegram webhook not registered: a valid webhook secret is required")
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    payload: dict[str, str] = {
        "url": f"{settings.public_base_url.rstrip('/')}/telegram/webhook",
        "drop_pending_updates": "false",
    }
    payload["secret_token"] = settings.telegram_webhook_secret
    retry_seconds = 5
    async with httpx.AsyncClient(timeout=15) as client:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.post(url, data=payload)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict) or result.get("ok") is not True:
                    raise RuntimeError("Telegram rejected webhook registration")
                logger.info("Telegram webhook registered")
                await _register_bot_commands(client, settings.telegram_bot_token)
                break
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Telegram webhook attempt %s failed with HTTP %s",
                    attempt,
                    exc.response.status_code,
                )
            except httpx.HTTPError as exc:
                logger.warning(
                    "Telegram webhook attempt %s failed (%s)",
                    attempt,
                    type(exc).__name__,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Telegram webhook attempt %s was rejected (%s)",
                    attempt,
                    type(exc).__name__,
                )
            await asyncio.sleep(retry_seconds)
            retry_seconds = min(retry_seconds * 2, 60)


async def _register_bot_commands(client: httpx.AsyncClient, token: str) -> None:
    base_url = f"https://api.telegram.org/bot{token}"
    commands = [
        {"command": "start", "description": "🏠 Open the command center"},
        {"command": "status", "description": "📊 View system dashboard"},
        {"command": "queue", "description": "📥 Review content queue"},
        {"command": "trends", "description": "🔥 View trend radar"},
        {"command": "analytics", "description": "📈 Performance analytics"},
        {"command": "sync", "description": "🔄 Sync YouTube analytics"},
        {"command": "platforms", "description": "🔗 Platform setup guide"},
        {"command": "providers", "description": "🧩 Configure media providers"},
        {"command": "automation", "description": "🤖 Run one automation cycle"},
        {"command": "retry", "description": "🔁 Retry a failed job"},
        {"command": "preview", "description": "▶️ Send a rendered video for review"},
        {"command": "generate", "description": "✨ Create a Hindi preview"},
        {"command": "pause", "description": "⏸️ Pause the factory"},
        {"command": "resume", "description": "▶️ Resume the factory"},
        {"command": "help", "description": "📚 Show all commands"},
    ]
    try:
        response = await client.post(
            f"{base_url}/deleteMyCommands",
            json={"scope": {"type": "default"}},
        )
        response.raise_for_status()
        for owner_id in sorted(get_settings().owner_ids):
            response = await client.post(
                f"{base_url}/setMyCommands",
                json={
                    "commands": commands,
                    "scope": {"type": "chat", "chat_id": owner_id},
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Telegram command menu registration failed with HTTP %s",
            exc.response.status_code,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Telegram command menu registration failed (%s)",
            type(exc).__name__,
        )


def _get_telegram_polling_offset() -> int | None:
    from .db import FactorySetting, SessionLocal

    with SessionLocal() as session:
        setting = session.get(FactorySetting, "telegram_polling_offset")
        if setting is None:
            return None
        try:
            return int(setting.value)
        except ValueError as exc:
            raise RuntimeError("Stored Telegram polling offset is invalid") from exc


def _save_telegram_polling_offset(offset: int) -> None:
    from .db import FactorySetting, SessionLocal

    with SessionLocal() as session:
        setting = session.get(FactorySetting, "telegram_polling_offset")
        if setting is None:
            session.add(
                FactorySetting(key="telegram_polling_offset", value=str(offset))
            )
        else:
            setting.value = str(offset)
        session.commit()


async def _poll_telegram_once(
    client: httpx.AsyncClient,
    token: str,
    offset: int | None,
) -> int | None:
    params: dict[str, Any] = {
        "timeout": 50,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    response = await client.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params=params,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Telegram rejected the polling request")
    updates = payload.get("result")
    if not isinstance(updates, list):
        raise RuntimeError("Telegram returned an invalid polling response")

    for update in updates:
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            raise RuntimeError("Telegram returned an invalid update")
        await handle_update(update)
        offset = update["update_id"] + 1
        await asyncio.to_thread(_save_telegram_polling_offset, offset)
    return offset


async def telegram_polling_loop() -> None:
    """Receive Telegram updates over outbound HTTPS for private RDP installs."""
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        logger.info("Telegram polling is disabled because no bot token is configured")
        return

    offset = await asyncio.to_thread(_get_telegram_polling_offset)
    timeout = httpx.Timeout(60.0, connect=15.0)
    retry_seconds = 2
    polling_ready = False
    async with httpx.AsyncClient(timeout=timeout) as client:
        delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        while True:
            if not polling_ready:
                try:
                    response = await client.post(
                        delete_url,
                        json={"drop_pending_updates": False},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or payload.get("ok") is not True:
                        raise RuntimeError(
                            "Telegram rejected webhook removal for polling"
                        )
                    await _register_bot_commands(client, token)
                    polling_ready = True
                    retry_seconds = 2
                except (httpx.HTTPError, RuntimeError) as exc:
                    logger.warning(
                        "Telegram polling startup failed (%s); retrying in %s seconds",
                        type(exc).__name__,
                        retry_seconds,
                    )
                    await asyncio.sleep(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 30)
                    continue
            try:
                offset = await _poll_telegram_once(client, token, offset)
                retry_seconds = 2
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "Telegram polling failed (%s); retrying in %s seconds",
                    type(exc).__name__,
                    retry_seconds,
                )
                await asyncio.sleep(retry_seconds)
                retry_seconds = min(retry_seconds * 2, 30)
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409:
                    polling_ready = False


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
        f"  Mode: {'Safe preview (mock)' if is_mock_publishing() else 'Live'}\n"
        f"  Publishing: 🔒 Approval required, always\n"
        f"\n"
        f"Pick an action below, or type /help for commands.\n"
        f"{_divider()}"
    )


def _build_status_message() -> str:
    settings = get_settings()
    from .platform_credentials import get_platform_secret, get_platform_value, platform_enabled

    paused = is_paused()
    state_icon = "🔴" if paused else "🟢"
    state_text = "PAUSED" if paused else "RUNNING"
    mock_icon = "🧪" if is_mock_publishing() else "🚀"
    mock_text = "Mock mode" if is_mock_publishing() else "Live mode"
    youtube = connection_status()
    youtube_state = "connected" if youtube["authorized"] else (
        "OAuth ready" if youtube["configured"] else "setup needed"
    )
    instagram_ready = bool(
        get_platform_secret("instagram") or settings.instagram_access_token
    ) and bool(
        get_platform_value("instagram", "user_id") or settings.instagram_user_id
    )
    instagram_active = platform_enabled("instagram", settings.instagram_enabled)
    workers = worker_status()
    worker_online = any(worker["online"] for worker in workers)

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
        f"  📸  Instagram — {'ready' if instagram_ready else 'needs setup'} · "
        f"{'enabled' if instagram_active else 'disabled'}\n"
        f"  ▶️  YouTube — {youtube_state} · "
        f"{'enabled' if platform_enabled('youtube', settings.youtube_enabled) else 'disabled'}\n"
        f"  🖥  Render worker — {'online' if worker_online else 'offline'}\n"
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
        f"<b>🎬 Content</b>\n"
        f"  /generate &lt;topic&gt; — Create a preview for the selected destination\n"
        f"  /preview &lt;job-id&gt; — Send a rendered video for manual review\n"
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
        f"  /platforms — Instagram and YouTube connection guide\n"
        f"  /providers — Choose script, image and voice providers\n"
        f"\n"
        f"<b>⚙️ Controls (headquarters)</b>\n"
        f"  /pause — Pause all automation\n"
        f"  /resume — Resume automation\n"
        f"  /menu — Show main menu\n"
        f"  ⚙️ Controls — Reel profile, publishing mode, limits and providers\n"
        f"\n"
        f"<b>💡 Tips</b>\n"
        f"  • All content requires approval before publishing\n"
        f"  • Use short job IDs (first 8 chars) with commands\n"
        f"  • The factory starts paused for safety\n"
        f"  • Live Instagram publishing must be explicitly enabled and every Reel approved\n"
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
        f"  /generate &lt;topic&gt; — create a preview for the selected destination\n"
        f"  /preview &lt;job-id&gt; — send the rendered video for review\n"
        f"  /approve &lt;job-id&gt; — approve after review\n"
        f"  /publish &lt;job-id&gt; — publish an approved job\n"
        f"\n"
        f"Everything is controlled from the buttons below — niche, "
        f"format, upload limits, providers and the factory switch. "
        f"Changes apply immediately, no redeploy.\n"
        f"\n"
        f"{_divider()}"
    )


def _build_platform_guide_message() -> str:
    callback_url = _youtube_callback_url()
    callback_text = (
        f"YouTube callback URL:\n<code>{html.escape(callback_url)}</code>\n\n"
        if callback_url
        else
        "YouTube callback URL will appear in its setup steps after "
        "<code>PUBLIC_BASE_URL</code> is configured in Railway.\n\n"
    )
    return (
        f"🔗 <b>Connect your channels</b>\n{_divider()}\n\n"
        "Choose <b>YouTube</b> or <b>Instagram Reels</b> below to open its setup wizard. "
        "The bot will show each step and check the connection when you return.\n\n"
        "<b>Before you start</b>\n"
        "Keep your Railway API service online. You will need to add YouTube's "
        "OAuth client values or Instagram's public HTTPS media URL in Railway "
        "once. Never paste Google client secrets into Telegram.\n\n"
        f"{callback_text}"
        "Connecting an account does not publish anything. Keep mock mode on "
        "until setup is verified; every real post still needs your approval.\n"
        f"{_divider()}"
    )


def _youtube_callback_url() -> str:
    settings = get_settings()
    if settings.youtube_redirect_uri:
        return settings.youtube_redirect_uri
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}/oauth/youtube/callback"
    return ""


def _build_youtube_setup_message(step: int = 1) -> str:
    callback_url = _youtube_callback_url()
    if step == 1:
        body = (
            "<b>Step 1 of 5 · Create a Google Cloud project</b>\n\n"
            "Open Google Cloud Console, sign in with the Google account you use "
            "for your channel, and create or select a project for this bot.\n\n"
            "Keep this same project selected for the next steps."
        )
        link_label, link = "☁️ Open Google Cloud Console", "https://console.cloud.google.com/"
    elif step == 2:
        body = (
            "<b>Step 2 of 5 · Turn on YouTube APIs</b>\n\n"
            "In the selected project, open APIs &amp; Services → Library. Enable "
            "<b>YouTube Data API v3</b> and <b>YouTube Analytics API</b>.\n\n"
            "Wait until both show as enabled before continuing."
        )
        link_label, link = "🧩 Open API Library", "https://console.cloud.google.com/apis/library"
    elif step == 3:
        body = (
            "<b>Step 3 of 5 · Configure Google consent</b>\n\n"
            "Open Google Auth Platform. Set the app name and support email, "
            "choose External if prompted, and add your channel owner's Google "
            "account as a test user while the app is in Testing.\n\n"
            "For public use, complete Google's verification requirements."
        )
        link_label, link = "🔐 Open Google Auth Platform", "https://console.cloud.google.com/auth/overview"
    elif step == 4:
        redirect = (
            f"<code>{html.escape(callback_url)}</code>"
            if callback_url
            else
            "<code>https://YOUR-RAILWAY-DOMAIN/oauth/youtube/callback</code> "
            "(configure <code>PUBLIC_BASE_URL</code> in Railway first)"
        )
        body = (
            "<b>Step 4 of 5 · Create the OAuth client</b>\n\n"
            "In Google Auth Platform → Clients, create an OAuth client with "
            "application type <b>Web application</b>. Under Authorized redirect "
            f"URIs, add this exact URL:\n{redirect}\n\n"
            "Copy the Client ID and Client secret. Never send the secret in Telegram."
        )
        link_label, link = "🪪 Open OAuth Clients", "https://console.cloud.google.com/auth/clients"
    elif step == 5:
        body = (
            "<b>Step 5 of 5 · Save keys and connect</b>\n\n"
            "In Railway → contentfactory API → Variables, add "
            "<code>YOUTUBE_CLIENT_ID</code>, <code>YOUTUBE_CLIENT_SECRET</code>, "
            "and <code>YOUTUBE_REDIRECT_URI</code> (the same callback URL from step 4). "
            "Save and wait for the API to redeploy.\n\n"
            "Then tap <b>Connect YouTube</b>, sign in to the channel owner's "
            "Google account, and approve access. Return here and verify the channel.\n\n"
            "Connecting does not enable posting. Keep publishing disabled until ready."
        )
        link_label, link = "🚂 Open Railway", "https://railway.com/dashboard"
    else:
        raise ValueError("YouTube setup step must be between 1 and 5")
    return (
        f"▶️ <b>YouTube setup</b> · {step}/5\n{_divider()}\n\n{body}\n\n"
        "🔒 OAuth tokens are stored encrypted. You can reconnect or disconnect "
        "the account later from Channel accounts."
    )


def _setup_step_keyboard(
    platform: str,
    step: int,
    total: int,
    link_label: str,
    link: str,
) -> dict[str, list[list[dict[str, str]]]]:
    prefix = "youtube" if platform == "youtube" else "instagram"
    rows = [[_link_button(link_label, link)]]
    navigation = []
    if step > 1:
        navigation.append(_button("⬅️ Previous", f"{prefix}_step:{step - 1}"))
    if step < total:
        navigation.append(_button("Next ➡️", f"{prefix}_step:{step + 1}"))
    if navigation:
        rows.append(navigation)
    if platform == "youtube" and step == total:
        rows.append([_button("🔗 Connect YouTube", "youtube_auth")])
    if platform == "instagram" and step == 5:
        rows.extend(
            [
                [_button("🪪 Add Instagram account ID", "instagram_id")],
                [_button("🔐 Exchange & save token", "instagram_token")],
            ]
        )
    if platform == "instagram" and step == total:
        rows.append([_button("✅ Verify Instagram account", "instagram_verify")])
    rows.append([_button("🔙 Channel accounts", "accounts")])
    return {"inline_keyboard": rows}


def _youtube_setup_keyboard(step: int = 1) -> dict[str, list[list[dict[str, str]]]]:
    links = {
        1: ("☁️ Open Google Cloud Console", "https://console.cloud.google.com/"),
        2: ("🧩 Open API Library", "https://console.cloud.google.com/apis/library"),
        3: ("🔐 Open Google Auth Platform", "https://console.cloud.google.com/auth/overview"),
        4: ("🪪 Open OAuth Clients", "https://console.cloud.google.com/auth/clients"),
        5: ("🚂 Open Railway", "https://railway.com/dashboard"),
    }
    label, url = links[step]
    return _setup_step_keyboard("youtube", step, 5, label, url)


def _platform_guide_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [_button("▶️ Set up / connect YouTube", "youtube_setup")],
            [_button("📸 Set up Instagram Reels", "instagram_setup_guide")],
            [_button("🔙 Back to Controls", "controls")],
        ]
    }


def _build_instagram_setup_message(step: int = 1) -> str:
    if step == 1:
        body = (
            "<b>Step 1 of 6 · Prepare the Instagram account</b>\n\n"
            "Use an Instagram <b>Professional (Business or Creator)</b> account "
            "and connect it to a Facebook Page you manage. In Instagram, check "
            "Settings → Account type and tools if you need to switch."
        )
        link_label, link = "📸 Open Instagram", "https://www.instagram.com/"
    elif step == 2:
        body = (
            "<b>Step 2 of 6 · Create the Meta app</b>\n\n"
            "Create an app in Meta for Developers. Add the Instagram Graph API "
            "product and connect your Facebook Page and Instagram Professional "
            "account. Request <code>instagram_basic</code>, "
            "<code>instagram_content_publish</code>, and "
            "<code>pages_read_engagement</code> permissions.\n\n"
            "In Railway → contentfactory API → Variables, add "
            "<code>META_APP_ID</code> and <code>META_APP_SECRET</code> from this "
            "Meta app, then wait for the service to redeploy. Never send the "
            "App Secret in Telegram.\n\n"
            "For testing, add your Facebook/Instagram account as an app role. "
            "Publishing to other users requires Meta app review."
        )
        link_label, link = "🧩 Open Meta for Developers", "https://developers.facebook.com/apps/"
    elif step == 3:
        body = (
            "<b>Step 3 of 6 · Create a long-lived token</b>\n\n"
            "Open Graph API Explorer, select your Meta app, choose <b>Get User "
            "Access Token</b>, and grant the three permissions from step 2. "
            "Copy the short-lived user token. In the next step, this bot will "
            "exchange it with Meta for a long-lived token using the App ID and "
            "Secret stored in Railway.\n\n"
            "Treat the token like a password. Paste it only into the bot's "
            "private prompt; never send it in a group."
        )
        link_label, link = "🧪 Open Graph API Explorer", "https://developers.facebook.com/tools/explorer/"
    elif step == 4:
        body = (
            "<b>Step 4 of 6 · Find the Instagram account ID</b>\n\n"
            "In Graph API Explorer, run:\n"
            "<code>me/accounts?fields=id,name,instagram_business_account</code>\n\n"
            "Find your Facebook Page in the result and copy the digits in "
            "<code>instagram_business_account.id</code>. This is the Instagram "
            "Professional Account ID (not your @username or Page ID)."
        )
        link_label, link = "🧪 Open Graph API Explorer", "https://developers.facebook.com/tools/explorer/"
    elif step == 5:
        body = (
            "<b>Step 5 of 6 · Add the account to this bot</b>\n\n"
            "Tap <b>Add Instagram account ID</b> and send the numeric ID. Then "
            "tap <b>Exchange &amp; save token</b> and paste the short-lived token "
            "from Graph API Explorer in the private prompt. The bot exchanges it "
            "with Meta and stores the returned long-lived token encrypted."
        )
        link_label, link = "📖 Reopen Meta app", "https://developers.facebook.com/apps/"
    elif step == 6:
        body = (
            "<b>Step 6 of 6 · Finish and verify</b>\n\n"
            "In Railway → contentfactory API → Variables, set "
            "<code>PUBLIC_BASE_URL</code> to the API's public HTTPS domain. Save "
            "and wait for redeploy; Meta must fetch your video and cover from it.\n\n"
            "Tap <b>Verify Instagram account</b>. When it shows the right "
            "username, use Channel accounts to enable publishing. Start in mock "
            "mode and approve every post yourself."
        )
        link_label, link = "🚂 Open Railway", "https://railway.com/dashboard"
    else:
        raise ValueError("Instagram setup step must be between 1 and 6")
    return (
        f"📸 <b>Instagram setup</b> · {step}/6\n{_divider()}\n\n{body}\n\n"
        "The bot supports one Instagram account at a time; you can replace or "
        "disconnect it anytime from Channel accounts."
    )


def _instagram_setup_step_keyboard(step: int = 1) -> dict[str, list[list[dict[str, str]]]]:
    links = {
        1: ("📸 Open Instagram", "https://www.instagram.com/"),
        2: ("🧩 Open Meta for Developers", "https://developers.facebook.com/apps/"),
        3: ("🧪 Open Graph API Explorer", "https://developers.facebook.com/tools/explorer/"),
        4: ("🧪 Open Graph API Explorer", "https://developers.facebook.com/tools/explorer/"),
        5: ("📖 Reopen Meta app", "https://developers.facebook.com/apps/"),
        6: ("🚂 Open Railway", "https://railway.com/dashboard"),
    }
    label, url = links[step]
    return _setup_step_keyboard("instagram", step, 6, label, url)


def _instagram_setup_keyboard() -> dict[str, list[dict[str, str]]]:
    return {
        "inline_keyboard": [
            [_link_button("🧩 Open Meta for Developers", "https://developers.facebook.com/apps/")],
            [_button("🪪 Add account ID", "instagram_id")],
            [_button("🔐 Set Instagram token", "instagram_token")],
            [_button("📖 Step-by-step setup", "instagram_setup_guide")],
            [_button("✅ Verify Instagram account", "instagram_verify")],
            [_button("🟢 Enable Instagram publishing", "instagram:enable")],
            [_button("⏸ Disable Instagram publishing", "instagram:disable")],
            [_button("🔙 Back to Controls", "controls")],
        ]
    }


def _account_manager_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .platform_credentials import platform_enabled

    youtube = connection_status()
    settings = get_settings()
    instagram_enabled = platform_enabled("instagram", settings.instagram_enabled)
    youtube_enabled = platform_enabled("youtube", settings.youtube_enabled)
    rows = [
        [_button("▶️ YouTube setup steps", "youtube_setup")],
    ]
    if youtube["authorized"]:
        rows.append([_button("🔄 Reconnect YouTube", "youtube_reconnect")])
    elif youtube["configured"]:
        rows.append([_button("🔗 Connect YouTube", "youtube_auth")])
    if youtube["authorized"]:
        rows.append([_button("🗑 Disconnect YouTube", "youtube_disconnect_confirm")])
    if youtube["authorized"]:
        rows.append([_button("✅ Verify YouTube channel", "youtube_verify")])
        rows.append([
            _button(
                "⏸ Disable YouTube" if youtube_enabled else "🟢 Enable YouTube",
                "youtube:disable" if youtube_enabled else "youtube:enable",
            )
        ])
    rows.extend(
        [
            [_button("📸 Instagram setup steps", "instagram_setup_guide")],
            [_button("🪪 Add / change Instagram ID", "instagram_id")],
            [_button("🔐 Add / replace Instagram token", "instagram_token")],
            [_button("✅ Verify Instagram account", "instagram_verify")],
            [_button("🗑 Disconnect Instagram", "instagram_disconnect_confirm")],
            [
                _button(
                    "⏸ Disable Instagram" if instagram_enabled else "🟢 Enable Instagram",
                    "instagram:disable" if instagram_enabled else "instagram:enable",
                )
            ],
            [_button("🔙 Back to Controls", "controls")],
        ]
    )
    return {"inline_keyboard": rows}


def _build_account_manager_message() -> str:
    from .platform_credentials import get_platform_secret, get_platform_value, platform_enabled

    settings = get_settings()
    youtube = connection_status()
    youtube_enabled = platform_enabled("youtube", settings.youtube_enabled)
    stored_instagram_id = get_platform_value("instagram", "user_id")
    instagram_id = stored_instagram_id or settings.instagram_user_id
    instagram_token_stored = bool(get_platform_secret("instagram"))
    instagram_token_env = bool(settings.instagram_access_token)
    instagram_token_configured = instagram_token_stored or instagram_token_env
    instagram_enabled = platform_enabled("instagram", settings.instagram_enabled)
    instagram_public_url = settings.public_base_url.startswith("https://")
    instagram_source = (
        "saved in bot" if instagram_token_stored
        else "set in Railway" if instagram_token_env
        else "not connected"
    )
    youtube_state = "connected" if youtube["authorized"] else (
        "OAuth configured; connect account" if youtube["configured"] else "setup required"
    )
    youtube_missing = [
        key for key, value in (
            ("YOUTUBE_CLIENT_ID", settings.youtube_client_id),
            ("YOUTUBE_CLIENT_SECRET", settings.youtube_client_secret),
        ) if not value
    ]
    youtube_setup_note = (
        f"   Missing in Railway: <code>{', '.join(youtube_missing)}</code>. "
        "Open YouTube setup steps → Step 5, add the values, and wait for redeploy.\n"
        if youtube_missing else ""
    )
    masked_instagram_id = (
        f"••••{html.escape(instagram_id[-4:])}" if instagram_id else "not added"
    )
    return (
        f"🔗 <b>Channel accounts</b>\n{_divider()}\n\n"
        "<i>One connected account per platform is supported.</i>\n\n"
        f"▶️ <b>YouTube:</b> {youtube_state}\n"
        f"   Publishing: {'enabled' if youtube_enabled else 'disabled'}\n"
        f"{youtube_setup_note}"
        f"📸 <b>Instagram:</b> "
        f"{'ready' if instagram_token_configured and instagram_id and instagram_public_url else 'setup required'}\n"
        f"   Account ID: {masked_instagram_id}\n"
        f"   Token: {instagram_source}\n"
        f"   Publishing: {'enabled' if instagram_enabled else 'disabled'}\n"
        f"   Public HTTPS URL: {'configured' if instagram_public_url else 'missing'}\n\n"
        "Use the buttons to connect, replace credentials, enable/disable posting, "
        "or disconnect an account. Disconnecting stops publishing for that platform."
        f"\n{_divider()}"
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
        f"A preview is created for "
        f"<b>{html.escape(PUBLISH_TARGETS[get_publish_target()])}</b> in the current format "
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
    chat = source.get("chat") or {}
    chat_id = chat.get("id")
    if not isinstance(user_id, int) or not isinstance(chat_id, int):
        return
    if chat.get("type") != "private":
        if callback and isinstance(callback.get("id"), str):
            await answer_callback(callback["id"])
        return
    if user_id not in get_settings().owner_ids or chat_id != user_id:
        if callback and isinstance(callback.get("id"), str):
            await answer_callback(callback["id"])
        if _claim_unauthorized_notice(user_id):
            try:
                await send_message(chat_id, "🚫 DO NOT DISTURB ME BETA !!")
            except httpx.HTTPError as exc:
                logger.warning(
                    "Could not send unauthorized-access notice (%s)",
                    type(exc).__name__,
                )
        return
    progress_message_id: int | None = None
    try:
        require_owner(user_id)

        # ── Callback query handling ─────────────────────────────────
        if callback:
            await answer_callback(str(callback.get("id", "")), "⏳ Working…")
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

                progress_message_id = await send_progress_message(
                    chat_id,
                    "🤖 <b>Automation is working…</b>\nChecking trends and preparing a job.",
                )
                outcome = run_automation_cycle()
                if outcome.get("skipped_reason"):
                    summary = f"⏭️ Automation skipped: {html.escape(str(outcome['skipped_reason']))}"
                else:
                    summary = (
                        f"🤖 <b>Automation cycle complete</b>\n"
                        f"   Platform: {html.escape(PUBLISH_TARGETS.get(str(outcome.get('platform')), str(outcome.get('platform'))))}\n"
                        f"   🔥 Trends considered: {outcome.get('trends_considered', 0)}\n"
                        f"   ✨ Jobs created: {outcome.get('jobs_created', 0)}\n"
                        f"   📝 Jobs generated: {outcome.get('jobs_generated', 0)}\n"
                        f"   🔁 Jobs requeued: {outcome.get('jobs_requeued', 0)}"
                    )
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    f"{summary}\n\nNothing was published — approval is still required.\n\n{_divider()}",
                    _automation_keyboard(),
                )
                progress_message_id = None

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
                    progress_message_id = await send_progress_message(
                        chat_id,
                        "🔄 <b>Syncing YouTube analytics…</b>\nFetching channel data from Google.",
                    )
                    result = sync_channel_analytics()
                    await finish_progress_message(
                        chat_id,
                        progress_message_id,
                        (
                            "✅ <b>YouTube analytics synced</b>\n"
                            f"{_divider()}\n\n"
                            f"Channel: <b>{html.escape(str(result['account']))}</b>\n"
                            f"Period: {result['start_date']} → {result['end_date']}\n\n"
                            "Open Analytics to view the latest snapshot."
                        ),
                        _keyboard(),
                    )
                    progress_message_id = None
                except Exception:
                    logger.exception("YouTube analytics sync failed")
                    await finish_progress_message(
                        chat_id,
                        progress_message_id,
                        "⚠️ Analytics sync is unavailable. Check YouTube authorization and try again.",
                        _keyboard(),
                    )
                    progress_message_id = None

            elif data == "platform_guide":
                await send_message(
                    chat_id,
                    _build_platform_guide_message(),
                    _platform_guide_keyboard(),
                )

            elif data == "accounts":
                await send_message(
                    chat_id,
                    _build_account_manager_message(),
                    _account_manager_keyboard(),
                )

            elif data == "youtube_verify":
                from .youtube_oauth import get_authorized_channel

                try:
                    channel = get_authorized_channel()
                except (httpx.HTTPError, RuntimeError) as exc:
                    logger.warning("YouTube account verification failed (%s)", type(exc).__name__)
                    await send_message(
                        chat_id,
                        "⚠️ <b>Could not verify the YouTube channel.</b>\n"
                        "Reconnect the correct Google account and confirm it owns a YouTube channel.",
                        _account_manager_keyboard(),
                    )
                else:
                    await send_message(
                        chat_id,
                        f"✅ <b>YouTube account verified</b>\n"
                        f"Channel: <b>{html.escape(channel['title'])}</b>\n"
                        f"Channel ID: <code>{html.escape(channel['id'])}</code>",
                        _account_manager_keyboard(),
                    )

            elif data == "instagram_verify":
                from .platform_credentials import verify_instagram_account

                try:
                    account = verify_instagram_account()
                except (httpx.HTTPError, RuntimeError) as exc:
                    logger.warning("Instagram account verification failed (%s)", type(exc).__name__)
                    await send_message(
                        chat_id,
                        "⚠️ <b>Could not verify the Instagram account.</b>\n"
                        "Check the account ID, long-lived token, and Meta API permissions.",
                        _account_manager_keyboard(),
                    )
                else:
                    username = f"@{account['username']}" if account["username"] else "Connected"
                    await send_message(
                        chat_id,
                        f"✅ <b>Instagram account verified</b>\n"
                        f"Account: <b>{html.escape(username)}</b>\n"
                        f"Account ID: <code>{html.escape(account['id'])}</code>",
                        _account_manager_keyboard(),
                    )

            elif data in {"youtube_auth", "youtube_reconnect"}:
                status = connection_status()
                if status["authorized"] and data == "youtube_auth":
                    await send_message(
                        chat_id,
                        _build_account_manager_message(),
                        _account_manager_keyboard(),
                    )
                elif not status["configured"]:
                    await send_message(
                        chat_id,
                        _build_youtube_setup_message(5),
                        _youtube_setup_keyboard(5),
                    )
                else:
                    try:
                        url = create_authorization_url()
                        await send_message(
                            chat_id,
                            "🔗 <b>Connect YouTube</b>\n\n"
                            "Tap below to sign in to Google and choose the channel you want to connect. "
                            "This private authorization link expires in ten minutes.",
                            {
                                "inline_keyboard": [
                                    [_link_button("🔐 Continue with Google", url)],
                                    [_button("📖 Setup steps", "youtube_setup")],
                                    [_button("✅ Check connection", "youtube_auth")],
                                    [_button("🔙 Back to Controls", "controls")],
                                ]
                            },
                        )
                    except RuntimeError as exc:
                        logger.warning("YouTube OAuth setup unavailable: %s", exc)
                        await send_message(
                            chat_id,
                            _build_youtube_setup_message(),
                            _youtube_setup_keyboard(),
                        )

            elif data == "youtube_disconnect_confirm":
                await send_message(
                    chat_id,
                    "⚠️ <b>Disconnect YouTube?</b>\n"
                    "This removes the saved Google authorization, disables YouTube publishing, "
                    "and asks Google to revoke access. You can connect again later.",
                    {
                        "inline_keyboard": [
                            [_button("🗑 Yes, disconnect YouTube", "youtube_disconnect")],
                            [_button("↩️ Keep connected", "accounts")],
                        ]
                    },
                )

            elif data == "youtube_disconnect":
                from .platform_credentials import set_platform_enabled
                from .youtube_oauth import disconnect_authorization

                removed, revoked = disconnect_authorization()
                set_platform_enabled("youtube", False)
                result = (
                    "Google access was revoked."
                    if revoked
                    else "Saved access was removed locally. Google revocation could not be confirmed; "
                    "you can also remove this app from your Google Account security settings."
                )
                if not removed:
                    result = "No saved YouTube authorization was found."
                await send_message(
                    chat_id,
                    f"✅ <b>YouTube disconnected.</b>\n{result}",
                    _account_manager_keyboard(),
                )

            elif data == "youtube_setup":
                await send_message(
                    chat_id,
                    _build_youtube_setup_message(1),
                    _youtube_setup_keyboard(1),
                )

            elif data.startswith("youtube_step:"):
                try:
                    step = int(data.split(":", 1)[1])
                    message = _build_youtube_setup_message(step)
                    keyboard = _youtube_setup_keyboard(step)
                except (ValueError, KeyError):
                    await send_message(chat_id, "⚠️ Invalid YouTube setup step.", _account_manager_keyboard())
                else:
                    await send_message(chat_id, message, keyboard)

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

            elif data == "reel_profile":
                await send_message(
                    chat_id,
                    _build_reel_profile_message(),
                    _reel_profile_keyboard(),
                )

            elif data.startswith("reel_duration:"):
                from .hq import set_reel_duration

                try:
                    seconds = set_reel_duration(int(data.split(":", 1)[1]))
                    await send_message(
                        chat_id,
                        f"✅ <b>Reel duration set to {seconds} seconds.</b>\nNew jobs use this target.",
                        _reel_profile_keyboard(),
                    )
                except ValueError as exc:
                    await send_message(chat_id, f"⚠️ {html.escape(str(exc))}", _reel_profile_keyboard())

            elif data.startswith("reel_voice:"):
                from .hq import set_reel_voice

                try:
                    voice = set_reel_voice(data.split(":", 1)[1])
                    await send_message(
                        chat_id,
                        f"✅ <b>Hindi narration voice set:</b> {html.escape(voice)}",
                        _reel_profile_keyboard(),
                    )
                except ValueError as exc:
                    await send_message(chat_id, f"⚠️ {html.escape(str(exc))}", _reel_profile_keyboard())

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

            elif data == "hq:publish_target":
                await send_message(
                    chat_id,
                    _build_publish_target_message(),
                    _publish_target_keyboard(),
                )

            elif data.startswith("publish_target:"):
                try:
                    target = set_publish_target(data.split(":", 1)[1])
                except ValueError:
                    await send_message(
                        chat_id,
                        "⚠️ Unsupported publishing destination.",
                        _publish_target_keyboard(),
                    )
                else:
                    await send_message(
                        chat_id,
                        f"✅ New jobs will target <b>{html.escape(PUBLISH_TARGETS[target])}</b>.",
                        _publish_target_keyboard(),
                    )

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

            elif data == "spending:toggle":
                if no_paid_providers_enabled():
                    await send_message(
                        chat_id,
                        "⚠️ <b>Allow billable providers?</b>\n\n"
                                        "This permits metered provider calls such as Vertex AI, OpenAI, and ElevenLabs "
                                        "and paid-provider failover. It can create charges; provider balances are not "
                                        "monitored. Gemini can also become billable if its Google project has billing enabled.\n\n"
                        "No-paid mode is the safer launch setting.",
                        {"inline_keyboard": [
                            [_button("⚠️ Confirm: allow paid providers", "spending:paid")],
                            [_button("🛑 Keep no-paid mode ON", "spending:no_paid")],
                        ]},
                    )
                else:
                    set_no_paid_providers_enabled(True)
                    await send_message(
                        chat_id,
                        "🛑 <b>No-paid mode enabled.</b>\n"
                        "Metered Vertex/OpenAI/ElevenLabs routes and automatic paid fallback are blocked. "
                        "Gemini is available only through its AI Studio API key; use a project with "
                        "billing disabled to prevent paid Gemini usage.",
                        _provider_keyboard(),
                    )

            elif data == "spending:paid":
                set_no_paid_providers_enabled(False)
                await send_message(
                    chat_id,
                    "⚠️ <b>Paid providers allowed.</b>\n"
                    "Metered Vertex/OpenAI/ElevenLabs generation and paid-provider fallback may incur charges.",
                    _provider_keyboard(),
                )

            elif data == "spending:no_paid":
                set_no_paid_providers_enabled(True)
                await send_message(
                    chat_id,
                    "🛑 <b>No-paid mode remains ON.</b>",
                    _provider_keyboard(),
                )

            elif data == "provider_credentials":
                await send_message(
                    chat_id,
                    _provider_credentials_message(),
                    _provider_credentials_keyboard(),
                )

            elif data == "video_pipeline":
                await send_message(
                    chat_id,
                    _provider_video_message(),
                    _provider_video_keyboard(),
                )

            elif data.startswith("render_worker:"):
                selection = data.split(":", 1)[1]
                railway_token = hashlib.sha256(
                    b"railway-renderer"
                ).hexdigest()[:16]
                target = (
                    "railway-renderer"
                    if selection in {"auto", railway_token}
                    else None
                )
                if target is None:
                    await send_message(
                        chat_id,
                        "⚠️ Only the Railway render worker is enabled. Deploy it or wait for it to come online.",
                        _provider_video_keyboard(),
                    )
                    return
                try:
                    set_render_worker_target(target)
                except ValueError:
                    logger.exception("Render worker selection was rejected")
                    await send_message(
                        chat_id,
                        "⚠️ Could not save the render worker selection.",
                        _provider_video_keyboard(),
                    )
                    return
                await send_message(
                    chat_id,
                    "✅ <b>Rendering is fixed to railway-renderer.</b>\n"
                    "New jobs will stay queued until that Railway worker is online.",
                    _provider_video_keyboard(),
                )

            elif data == "elevenlabs_voice_id":
                _pending_elevenlabs_voice_id.add(chat_id)
                await send_message(
                    chat_id,
                    "🎙️ <b>Set ElevenLabs voice ID</b>\n\n"
                    "Copy the voice ID from the selected voice's page in your ElevenLabs account "
                    "and send it here. It is an identifier, not your API key. "
                    "The ID is saved with factory settings. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data.startswith("provider_role:"):
                role = data.split(":", 1)[1]
                if role not in PROVIDER_ROLES:
                    await send_message(chat_id, "⚠️ Unsupported generation role.", _provider_keyboard())
                    return
                await send_message(
                    chat_id,
                    _provider_role_message(role),
                    _provider_role_keyboard(role),
                )

            elif data.startswith("provider_role_set:"):
                parts = data.split(":")
                if len(parts) != 3:
                    await send_message(chat_id, "⚠️ Invalid provider selection.", _provider_keyboard())
                    return
                _, role, provider = parts
                status = provider_status()
                if provider == "huggingface" and not status.get("huggingface"):
                    await send_message(
                        chat_id,
                        "⚠️ Hugging Face is not ready yet. Add its user access token "
                        "under API credentials first.",
                        _provider_role_keyboard(role),
                    )
                    return
                if provider == "huggingface" and role == "image" and not get_huggingface_image_endpoint():
                    await send_message(
                        chat_id,
                        "⚠️ Set the Hugging Face GPU Inference Endpoint URL before selecting FLUX.",
                        _provider_role_keyboard(role),
                    )
                    return
                if provider == "elevenlabs" and (
                    not status.get("elevenlabs") or not get_elevenlabs_voice_id()
                ):
                    await send_message(
                        chat_id,
                        "⚠️ ElevenLabs is not ready yet. Add its API key and voice ID "
                        "under Voice settings first.",
                        _provider_role_keyboard(role),
                    )
                    return
                try:
                    set_provider_for_role(role, provider)
                except ValueError:
                    await send_message(chat_id, "⚠️ Unsupported provider selection.", _provider_keyboard())
                    return
                await send_message(
                    chat_id,
                    f"✅ <b>{html.escape(role.title())} set to "
                    f"{html.escape(PROVIDER_ROLES[role][provider])}</b>\n"
                    "New jobs will use this choice.",
                    _provider_role_keyboard(role),
                )

            elif data == "huggingface_image_endpoint":
                _pending_huggingface_image_endpoint.add(chat_id)
                await send_message(
                    chat_id,
                    "🔗 <b>Set the Hugging Face image endpoint</b>\n\n"
                    "Send the base HTTPS URL ending in <code>.endpoints.huggingface.cloud</code>. "
                    "Use the endpoint created for FLUX.1 Schnell; do not include a path or query string. "
                    "GPU hosting is billed separately from Railway. Press /cancel to abort.",
                    _back_keyboard(),
                )

            elif data == "instagram_id":
                _pending_instagram_id.add(chat_id)
                await send_message(
                    chat_id,
                    "🪪 <b>Add your Instagram account ID</b>\n"
                    "Send the numeric Instagram Professional Account ID in this private chat. "
                    "To cancel, send /cancel.",
                    _back_keyboard(),
                )

            elif data == "instagram_setup":
                from .platform_credentials import get_platform_secret, get_platform_value, platform_enabled

                configured = bool(get_platform_secret("instagram") or get_settings().instagram_access_token)
                user_configured = bool(get_platform_value("instagram", "user_id") or get_settings().instagram_user_id)
                active = platform_enabled("instagram", get_settings().instagram_enabled)
                public_url_configured = bool(get_settings().public_base_url.startswith("https://"))
                await send_message(
                    chat_id,
                    (
                        "📸 <b>Instagram Reels setup</b>\n"
                        f"{_divider()}\n\n"
                        f"Account ID: <b>{'✅ added' if user_configured else '⚪ not added'}</b>\n"
                        f"Access token: <b>{'✅ saved' if configured else '⚪ not saved'}</b>\n"
                        f"Public HTTPS URL: <b>{'✅ configured' if public_url_configured else '⚪ missing'}</b>\n"
                        f"Publishing: <b>{'🟢 enabled' if active else '⚪ disabled'}</b>\n\n"
                        "Use the buttons to add missing details or open the guide. "
                        "Your token is encrypted and its Telegram message is deleted after saving."
                    ),
                    _instagram_setup_keyboard(),
                )

            elif data == "instagram_setup_guide":
                await send_message(
                    chat_id,
                    _build_instagram_setup_message(1),
                    _instagram_setup_step_keyboard(1),
                )

            elif data.startswith("instagram_step:"):
                try:
                    step = int(data.split(":", 1)[1])
                    message = _build_instagram_setup_message(step)
                    keyboard = _instagram_setup_step_keyboard(step)
                except (ValueError, KeyError):
                    await send_message(chat_id, "⚠️ Invalid Instagram setup step.", _account_manager_keyboard())
                else:
                    await send_message(chat_id, message, keyboard)

            elif data == "publishing_mode":
                live = not is_mock_publishing()
                await send_message(
                    chat_id,
                    (
                        f"🧪 <b>Publishing mode</b>\n{_divider()}\n\n"
                        f"Current: <b>{'LIVE' if live else 'MOCK / SAFE'}</b>\n"
                        "Mock mode never calls Instagram or YouTube. Live mode calls an official API "
                        "only when the platform is enabled and a job has current-version approval.\n\n"
                        "Switching to live requires a second confirmation."
                    ),
                    {"inline_keyboard": [
                        [_button("⚠️ Confirm live mode", "publishing_mode:live")],
                        [_button("🧪 Keep safe mock mode", "publishing_mode:mock")],
                        [_button("🔙 Back to Controls", "controls")],
                    ]},
                )

            elif data in {"publishing_mode:live", "publishing_mode:mock"}:
                enable_live = data == "publishing_mode:live"
                set_mock_publishing(not enable_live)
                await send_message(
                    chat_id,
                    (
                        "⚠️ <b>Live mode enabled.</b> Platform enablement, credentials, quality checks, "
                        "and explicit approval are still required."
                        if enable_live
                        else "🧪 <b>Safe mock mode enabled.</b> No platform API calls will be made."
                    ),
                    _controls_keyboard(),
                )

            elif data in {
                "instagram:enable", "instagram:disable",
                "youtube:enable", "youtube:disable",
            }:
                from .platform_credentials import get_platform_secret, get_platform_value, set_platform_enabled

                platform, action = data.split(":")
                enabled = action == "enable"
                if enabled and platform == "instagram":
                    instagram_ready = bool(
                        get_platform_secret("instagram") or get_settings().instagram_access_token
                    ) and bool(
                        get_platform_value("instagram", "user_id") or get_settings().instagram_user_id
                    ) and get_settings().public_base_url.startswith("https://")
                    if not instagram_ready:
                        await send_message(
                            chat_id,
                            "⚠️ <b>Instagram is not ready yet.</b>\n"
                            "Add the account ID and long-lived token, then set the public HTTPS "
                            "URL in Railway. Nothing has been enabled.",
                            _instagram_setup_keyboard(),
                        )
                        return
                if enabled and platform == "youtube" and not connection_status()["authorized"]:
                    await send_message(
                        chat_id,
                        "⚠️ <b>Connect a YouTube account first.</b>\n"
                        "Complete the setup steps and authorize the channel before enabling publishing.",
                        _youtube_setup_keyboard(),
                    )
                    return
                set_platform_enabled(platform, enabled)
                manager_keyboard = _account_manager_keyboard()
                await send_message(
                    chat_id,
                    (
                        f"{'🟢' if enabled else '⏸'} {platform.title()} publishing "
                        f"{'enabled' if enabled else 'disabled'}.\n"
                        "A Reel still cannot publish without explicit approval for its current version."
                    ),
                    manager_keyboard,
                )

            elif data == "instagram_disconnect_confirm":
                await send_message(
                    chat_id,
                    "⚠️ <b>Disconnect Instagram?</b>\n"
                    "This removes the token and account ID saved in the bot and disables publishing. "
                    "Values configured directly in Railway must be removed there separately.",
                    {
                        "inline_keyboard": [
                            [_button("🗑 Yes, disconnect Instagram", "instagram_disconnect")],
                            [_button("↩️ Keep connected", "accounts")],
                        ]
                    },
                )

            elif data == "instagram_disconnect":
                from .platform_credentials import remove_platform_account

                remove_platform_account("instagram")
                await send_message(
                    chat_id,
                    "✅ <b>Instagram disconnected in the bot.</b>\n"
                    "Publishing is disabled. If you also added Instagram credentials in Railway "
                    "Variables, remove them there and revoke or replace the token in Meta to fully disconnect.",
                    _account_manager_keyboard(),
                )

            elif data == "instagram_token":
                _pending_platform_setup[chat_id] = "instagram"
                await send_message(
                    chat_id,
                    "🔐 <b>Send your short-lived Instagram user token</b>\n"
                    "The bot will exchange it with Meta for a long-lived token, "
                    "store that token encrypted, and delete this message. "
                    "Never send your Meta App Secret here. Press /cancel to abort.",
                    _back_keyboard(),
                )

            elif data == "system_health":
                from .automation import automation_enabled
                from .platform_credentials import get_platform_secret

                workers = worker_status()
                online = [worker["worker_id"] for worker in workers if worker["online"]]
                ready_state = "not configured"
                if get_settings().public_base_url:
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            response = await client.get(f"{get_settings().public_base_url.rstrip('/')}/ready")
                        ready_state = "ready" if response.is_success else f"HTTP {response.status_code}"
                    except httpx.HTTPError:
                        ready_state = "unreachable"
                worker_state = ", ".join(html.escape(str(worker_id)) for worker_id in online) or "offline"
                configured = bool(
                        get_platform_secret("instagram")
                        or get_settings().instagram_access_token
                )
                await send_message(
                        chat_id,
                        (
                            f"🖥 <b>System health</b>\n{_divider()}\n\n"
                            f"API + database: <b>{html.escape(ready_state)}</b>\n"
                            f"Render worker: <b>{worker_state}</b>\n"
                            f"Instagram token: <b>{'configured' if configured else 'missing'}</b>\n"
                            f"Factory: <b>{'paused' if is_paused() else 'running'}</b>\n"
                            f"Automation: <b>{'on' if automation_enabled() else 'off'}</b>\n\n"
                        "If no worker is online, check the Railway factory-worker service "
                        "or optional RDP worker, confirm FFmpeg is installed, and verify "
                        "WORKER_TOKEN and FACTORY_URL match the API."
                    ),
                    _back_keyboard(),
                )

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
                    f"🎯 <b>{html.escape(str(PROVIDER_INFO[provider]['label']))} is now the default.</b>\n\nNew roles without their own selection will use this provider where it is supported.",
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
                progress_message_id = await send_progress_message(
                    chat_id,
                    "✍️ <b>Revising this Reel…</b>\nGenerating a new version and dispatching it to the render worker.",
                )
                job = generate_content(revise_job(job_id).id)
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    (
                        f"✍️ <b>Revision queued</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        "A new content version is rendering. Previous approval is invalidated.\n\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job.id),
                )
                progress_message_id = None

            elif data.startswith("revise:"):
                job_id = data.split(":", 1)[1]
                progress_message_id = await send_progress_message(
                    chat_id,
                    "✍️ <b>Revising this Reel…</b>\nGenerating a new version and dispatching it to the render worker.",
                )
                job = generate_content(revise_job(job_id).id)
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    (
                        f"✍️ <b>New Reel version queued</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        "The previous approval is invalidated. Review the new render before approving."
                    ),
                    _job_keyboard(job.id),
                )
                progress_message_id = None

            elif data.startswith("cancel:"):
                job_id = data.split(":", 1)[1]
                job = cancel_job(job_id)
                await send_message(
                    chat_id,
                    f"🗑 <b>Job cancelled</b>\n{_divider()}\n\n{_format_job_card(job)}",
                    _back_keyboard(),
                )

            elif data.startswith("schedule:"):
                job_id = data.split(":", 1)[1]
                _pending_schedule[chat_id] = job_id
                await send_message(
                    chat_id,
                    "⏰ <b>Schedule a publish reminder</b>\n"
                    "Send the future time in India time as <code>YYYY-MM-DD HH:MM</code>.\n"
                    "At that time the bot will ask you to publish; it will never auto-post.",
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
                progress_message_id = await send_progress_message(
                    chat_id,
                    "📤 <b>Publishing request in progress…</b>\nChecking approval, factory state, and platform response.",
                )
                job = publish_job(job_id)
                mock_label = " (mock)" if is_mock_publishing() else ""
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    (
                        f"📤 <b>Published{mock_label}</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        f"{_divider()}"
                    ),
                    _keyboard(),
                )
                progress_message_id = None

            elif data.startswith("preview:"):
                job_id = data.split(":", 1)[1]
                await _send_video_preview(chat_id, job_id)

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
                    job_snapshot = {
                        "platform": job.platform,
                        "metadata_json": job.metadata_json,
                        "topic": job.topic,
                    }
                try:
                    job_metadata = json.loads(job_snapshot["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    job_metadata = {}
                covers = job_metadata.get("thumbnails") or {}
                preview_platform = (
                    "instagram" if job_snapshot["platform"] == "instagram"
                    else "youtube" if job_snapshot["platform"] == "youtube"
                    else "instagram"
                )
                preview_name = covers.get(preview_platform)
                if isinstance(preview_name, str) and Path(preview_name).name == preview_name:
                    preview_path = (Path(get_settings().media_root).resolve() / preview_name).resolve()
                    if preview_path.parent == Path(get_settings().media_root).resolve() and preview_path.is_file():
                        try:
                            await send_photo(
                                chat_id,
                                preview_path,
                                f"Thumbnail preview · {job_snapshot['topic'][:120]}",
                            )
                        except httpx.HTTPError as exc:
                            logger.warning(
                                "Could not send thumbnail preview to Telegram (%s)",
                                type(exc).__name__,
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
                        f"{_publish_package_summary(job)}\n\n"
                        f"  <b>Blockers:</b>\n{blockers_text}\n\n"
                        f"  <b>Warnings (fix to earn better):</b>\n{warnings_text}\n\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job_id),
                )

            return

        # ── Text command handling ───────────────────────────────────
        text = (message.get("text") or "").strip()

        if chat_id in _pending_schedule:
            if text == "/cancel":
                _pending_schedule.pop(chat_id, None)
                await send_message(chat_id, "↩️ Schedule reminder cancelled.", _back_keyboard())
                return
            job_id = _pending_schedule.pop(chat_id)
            try:
                local_time = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(
                    tzinfo=ZoneInfo("Asia/Kolkata")
                )
                job = schedule_job(job_id, local_time)
                scheduled_at = job.scheduled_at
                if scheduled_at is not None and scheduled_at.tzinfo is None:
                    scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
                await send_message(
                    chat_id,
                    (
                        f"⏰ <b>Publish reminder scheduled</b>\n"
                        f"Reel: {html.escape(job.topic)}\n"
                        f"Time: {scheduled_at.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M IST')}\n"
                        "A Telegram reminder will arrive then. Publishing still needs your tap."
                    ),
                    _job_keyboard(job.id),
                )
            except (ValueError, TypeError) as exc:
                await send_message(chat_id, f"⚠️ {html.escape(str(exc))}", _keyboard())
            return

        if chat_id in _pending_platform_setup:
            if text == "/cancel":
                _pending_platform_setup.pop(chat_id, None)
                await send_message(chat_id, "↩️ Instagram setup cancelled.", _back_keyboard())
                return
            _pending_platform_setup.pop(chat_id)
            message_id = message.get("message_id")
            message_deleted = False
            if isinstance(message_id, int) and get_settings().telegram_bot_token:
                try:
                    await delete_message(chat_id, message_id)
                    message_deleted = True
                except Exception as exc:
                    logger.warning(
                        "Could not delete platform credential message (%s)",
                        type(exc).__name__,
                    )
            try:
                from .platform_credentials import exchange_instagram_token

                expires_in = await exchange_instagram_token(text)
                expiry_note = (
                    f" Meta reports it expires in about {max(1, expires_in // 86400)} days."
                    if expires_in
                    else ""
                )
                deletion_note = (
                    ""
                    if message_deleted
                    else "\n⚠️ Telegram could not delete your token message. Please delete it manually now."
                )
                await send_message(
                    chat_id,
                    "✅ <b>Instagram token saved encrypted.</b>\n"
                    f"{'The token message was deleted.' if message_deleted else 'The token was stored securely.'}"
                    f"{expiry_note}{deletion_note}\n"
                    "Next, verify your account from Channel accounts.",
                    _account_manager_keyboard(),
                )
            except ValueError as exc:
                deletion_note = (
                    ""
                    if message_deleted
                    else "\n⚠️ Delete the token message manually from this chat."
                )
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}{deletion_note}\n"
                    "Reopen Instagram setup and try again.",
                    _instagram_setup_keyboard(),
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                logger.warning("Instagram token exchange failed (%s)", type(exc).__name__)
                deletion_note = (
                    ""
                    if message_deleted
                    else "\n⚠️ Delete the token message manually from this chat."
                )
                await send_message(
                    chat_id,
                    "⚠️ <b>Meta could not exchange that token.</b>\n"
                    "Check that META_APP_ID and META_APP_SECRET are set in Railway, "
                    "the token is fresh, and the required permissions were granted. "
                    "Then request a new token and try again."
                    f"{deletion_note}",
                    _instagram_setup_keyboard(),
                )
            return

        if chat_id in _pending_instagram_id:
            if text == "/cancel":
                _pending_instagram_id.discard(chat_id)
                await send_message(
                    chat_id,
                    "↩️ Instagram account ID setup cancelled.",
                    _back_keyboard(),
                )
                return
            try:
                from .platform_credentials import set_platform_value

                set_platform_value("instagram", "user_id", text)
                _pending_instagram_id.discard(chat_id)
                await send_message(
                    chat_id,
                    "✅ <b>Instagram account ID saved.</b>\n"
                    "Next, add your long-lived token and check setup.",
                    _instagram_setup_keyboard(),
                )
            except ValueError as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}\n"
                    "Send the numeric account ID or /cancel.",
                    _back_keyboard(),
                )
            return

        if text.lower().startswith("id "):
            from .platform_credentials import set_platform_value

            try:
                set_platform_value("instagram", "user_id", text[3:].strip())
                await send_message(chat_id, "✅ <b>Instagram account ID saved.</b>", _back_keyboard())
            except ValueError as exc:
                await send_message(chat_id, f"⚠️ {html.escape(str(exc))}", _back_keyboard())
            return

        if chat_id in _pending_huggingface_image_endpoint:
            if text == "/cancel":
                _pending_huggingface_image_endpoint.discard(chat_id)
                await send_message(
                    chat_id,
                    "↩️ Hugging Face image endpoint setup cancelled.",
                    _provider_role_keyboard("image"),
                )
                return
            try:
                set_huggingface_image_endpoint(text)
                _pending_huggingface_image_endpoint.discard(chat_id)
                message_id = message.get("message_id")
                if isinstance(message_id, int):
                    try:
                        await delete_message(chat_id, message_id)
                    except Exception as exc:
                        logger.warning(
                            "Could not delete Hugging Face endpoint message (%s)",
                            type(exc).__name__,
                        )
                await send_message(
                    chat_id,
                    "✅ <b>Hugging Face image endpoint saved.</b>\n"
                    "Add the Hugging Face access token under API credentials, then select "
                    "FLUX.1 Schnell under Controls → Providers → Image. "
                    "The GPU provider bills separately from Railway.",
                    _provider_role_keyboard("image"),
                )
            except ValueError as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}\nSend the endpoint base URL or /cancel.",
                    _back_keyboard(),
                )
            return

        if chat_id in _pending_elevenlabs_voice_id:
            if text == "/cancel":
                _pending_elevenlabs_voice_id.discard(chat_id)
                await send_message(chat_id, "↩️ ElevenLabs voice ID setup cancelled.", _provider_keyboard())
                return
            try:
                set_elevenlabs_voice_id(text)
                _pending_elevenlabs_voice_id.discard(chat_id)
                await send_message(
                    chat_id,
                    "✅ <b>ElevenLabs voice ID saved.</b>\n"
                    "Choose ElevenLabs in Controls → Providers → Voice. "
                    "It is blocked while no-paid mode is ON and can consume plan credits.",
                    _provider_role_keyboard("voice"),
                )
            except ValueError as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}\nSend the voice ID or /cancel.",
                    _back_keyboard(),
                )
            return

        if chat_id in _pending_provider_setup:
            if text == "/cancel":
                _pending_provider_setup.pop(chat_id, None)
                await send_message(chat_id, "↩️ Provider setup cancelled.", _provider_keyboard())
                return
            provider = _pending_provider_setup.pop(chat_id)
            try:
                set_provider_secret(provider, text)
                if provider != "huggingface":
                    select_provider(provider)
                if provider == "gemini":
                    set_provider_for_role("script", "gemini")
                elif provider == "elevenlabs":
                    set_provider_for_role("voice", "elevenlabs")
                message_id = message.get("message_id")
                if isinstance(message_id, int):
                    try:
                        await delete_message(chat_id, message_id)
                    except Exception as exc:
                        logger.warning(
                            "Could not delete provider credential message (%s)",
                            type(exc).__name__,
                        )
                provider_label = html.escape(str(PROVIDER_INFO.get(provider, {}).get("label", provider.title())))
                wiring_note = (
                    "Script generation will use Gemini in the no-paid launch mode."
                    if provider == "gemini"
                    else "Hugging Face access is ready for its configured model routes. The FLUX image endpoint is billed separately; choose it only after setting the endpoint URL and reviewing its GPU pricing."
                    if provider == "huggingface"
                    else "Set the voice ID under Controls → Providers → Voice. "
                    "ElevenLabs usage consumes plan credits and is blocked by no-paid mode."
                    if provider == "elevenlabs"
                    else "Media generation will use this provider when AI media is enabled."
                    if PROVIDER_INFO.get(provider, {}).get("wired")
                    else "Its key is stored safely. The generation endpoint for this provider "
                    "gets connected when you activate the account — you can switch back to a "
                    "connected provider here at any time."
                )
                provider_saved_message = (
                    "✅ <b>Hugging Face key saved (encrypted)</b>"
                    if provider == "huggingface"
                    else f"✅ <b>{provider_label} — key saved (encrypted) and set as active</b>"
                )
                await send_message(
                    chat_id,
                    f"{provider_saved_message}\n\n"
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

        elif text == "/preview":
            await send_message(
                chat_id,
                "▶️ Send a rendered video for review with <code>/preview &lt;job-id&gt;</code>.\n"
                "The job must be ready for approval. You can use its full ID or first 8 characters.",
                _keyboard(),
            )

        elif text.startswith("/preview "):
            await _send_video_preview(chat_id, text[9:].strip())

        elif text == "/trends":
            await send_message(chat_id, _build_trends_message(), _keyboard())

        elif text == "/analytics":
            await send_message(chat_id, _build_analytics_message(), _keyboard())

        elif text == "/sync":
            try:
                progress_message_id = await send_progress_message(
                    chat_id,
                    "🔄 <b>Syncing YouTube analytics…</b>\nFetching channel data from Google.",
                )
                result = sync_channel_analytics()
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    (
                        "✅ <b>YouTube analytics synced</b>\n"
                        f"{_divider()}\n\n"
                        f"📺 Channel: <b>{html.escape(str(result['account']))}</b>\n"
                        f"📅 Period: {result['start_date']} → {result['end_date']}\n\n"
                        "Use /analytics to view the latest snapshot."
                    ),
                    _keyboard(),
                )
                progress_message_id = None
            except Exception:
                logger.exception("YouTube analytics sync failed")
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    "⚠️ Analytics sync is unavailable. Connect YouTube and try again.",
                    _keyboard(),
                )
                progress_message_id = None

        elif text == "/platforms":
            await send_message(chat_id, _build_platform_guide_message(), _back_keyboard())

        elif text == "/providers":
            await send_message(chat_id, _provider_message(), _provider_keyboard())

        elif text.startswith("/generate "):
            topic = text[10:].strip()
            if not topic:
                await send_message(chat_id, "⚠️ Please provide a topic: /generate &lt;topic&gt;", _keyboard())
                return
            progress_message_id = await send_progress_message(
                chat_id,
                f"✨ <b>Creating for {html.escape(PUBLISH_TARGETS[get_publish_target()])}…</b>\n"
                "Writing the script and preparing media for the render worker.",
            )
            update_id = update.get("update_id")
            request_id = f"telegram:{update_id}" if isinstance(update_id, int) else None
            job = create_job(topic, get_publish_target(), request_id=request_id)
            if job.status == "queued" and job.stage == "topic":
                job = generate_content(job.id)
                response = (
                    f"✨ <b>Preview created</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"⚙️ <b>Processing stages:</b>\n"
                    f"   ├ 📝 Script generated:  [██████████] 100%\n"
                    f"   ├ 🎞️ Video render:     [░░░░░░░░░░] Pending worker claim\n"
                    f"   └ 🛡️ Quality gate:      [░░░░░░░░░░]   0% (Pending)\n\n"
                    f"🎞️ Rendering starts when railway-renderer claims this queued job; "
                    f"this card does not show live render progress.\n"
                    f"{_divider()}"
                )
            else:
                response = (
                    f"ℹ️ <b>This request was already processed</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    "No duplicate generation was started."
                )
            await finish_progress_message(
                chat_id,
                progress_message_id,
                response,
                _job_keyboard(job.id),
            )
            progress_message_id = None

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
            progress_message_id = await send_progress_message(
                chat_id,
                "📤 <b>Publishing request in progress…</b>\nChecking approval, factory state, and platform response.",
            )
            job = publish_job(job_id)
            mock_label = " (mock)" if is_mock_publishing() else ""
            await finish_progress_message(
                chat_id,
                progress_message_id,
                (
                    f"📤 <b>Published{mock_label}</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    f"⚙️ <b>Processing stages:</b>\n"
                    f"   └ 🚀 Live published:    [██████████] 100% (Complete)\n\n"
                    f"{_divider()}"
                ),
                _keyboard(),
            )
            progress_message_id = None

        elif text == "/automation":
            from .automation import run_automation_cycle

            progress_message_id = await send_progress_message(
                chat_id,
                "🤖 <b>Automation is working…</b>\nChecking trends and preparing a job.",
            )
            outcome = run_automation_cycle()
            if outcome.get("skipped_reason"):
                summary = f"⏭️ Automation skipped: {html.escape(str(outcome['skipped_reason']))}"
            else:
                summary = (
                    f"🤖 <b>Automation cycle complete</b>\n"
                    f"   Platform: {html.escape(PUBLISH_TARGETS.get(str(outcome.get('platform')), str(outcome.get('platform'))))}\n"
                    f"   🔥 Trends considered: {outcome.get('trends_considered', 0)}\n"
                    f"   ✨ Jobs created: {outcome.get('jobs_created', 0)}\n"
                    f"   📝 Jobs generated: {outcome.get('jobs_generated', 0)}\n"
                    f"   🔁 Jobs requeued: {outcome.get('jobs_requeued', 0)}"
                )
            await finish_progress_message(
                chat_id,
                progress_message_id,
                f"{summary}\n\nContent still requires rendering and approval — nothing was published.\n\n{_divider()}",
                _automation_keyboard(),
            )
            progress_message_id = None

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
        logger.error("Telegram update failed (%s)", type(exc).__name__)
        safe_msg = _safe_error_text(exc)
        await finish_progress_message(
            chat_id,
            progress_message_id,
            (
                f"⚠️ <b>Action not completed</b>\n{_divider()}\n\n"
                f"{safe_msg}\n\n"
                f"No publication was triggered.\n"
                f"{_divider()}"
            ),
            _keyboard(),
        )
