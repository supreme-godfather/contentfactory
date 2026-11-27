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
from .telegram_icons import (
    ICON_SLOTS,
    clear_icon_id,
    get_icon_configuration,
    has_custom_icons,
    icon_button,
    set_icon_id,
    set_icons_enabled,
    unicode_fallback_markup,
)
from .security import require_owner
from .discovery import analytics_recommendations, analytics_summary, list_trends
from .clipping import (
    create_clip_job,
    list_clip_candidates,
    list_clip_jobs,
    promote_clip_candidate,
    retry_clip_job,
    review_clip_candidate,
)
from .services import (
    approve_job,
    approve_script,
    cancel_job,
    create_job,
    create_custom_jobs,
    generate_assets,
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
    FISH_AUDIO_MODELS,
    ELEVENLABS_TTS_MODELS,
    MINIMAX_SPEECH_MODELS,
    PROVIDER_INFO,
    PROVIDER_ROLES,
    delete_provider_secret,
    get_elevenlabs_voice_id,
    get_elevenlabs_tts_model,
    get_fish_audio_model_id,
    get_fish_audio_reference_id,
    get_huggingface_image_endpoint,
    get_minimax_speech_model,
    get_minimax_voice_id,
    get_provider_for_role,
    media_generation_enabled,
    no_paid_providers_enabled,
    provider_status,
    select_provider,
    set_elevenlabs_voice_id,
    set_elevenlabs_tts_model,
    set_fish_audio_model_id,
    set_fish_audio_reference_id,
    set_huggingface_image_endpoint,
    set_media_generation_enabled,
    set_minimax_speech_model,
    set_minimax_voice_id,
    set_no_paid_providers_enabled,
    set_provider_for_role,
    set_provider_secret,
)

logger = logging.getLogger(__name__)
_pending_provider_setup: dict[int, str] = {}
_pending_elevenlabs_voice_id: set[int] = set()
_pending_fish_audio_reference_id: set[int] = set()
_pending_huggingface_image_endpoint: set[int] = set()
_pending_minimax_voice_id: set[int] = set()
_pending_platform_setup: dict[int, str] = {}
_pending_account_name: dict[int, str] = {}
_pending_audio_upload: dict[int, str] = {}
_pending_clip_source: dict[int, tuple[str, int | None]] = {}
_pending_custom_script: dict[int, tuple[str, int]] = {}
_pending_custom_voice: dict[int, tuple[str, int]] = {}
_pending_custom_cover: dict[int, tuple[str, int, str]] = {}
_pending_watermark_text: set[int] = set()
_pending_auto_cost: set[int] = set()
_pending_icon_slot: dict[int, str] = {}
_pending_custom_creator: set[int] = set()
_pending_custom_part_count: dict[int, int] = {}
_pending_instagram_id: set[int] = set()
_pending_schedule: dict[int, str] = {}
_pending_series_topic: dict[int, str] = {}
_pending_series_pack: dict[int, str] = {}
_pending_credit_input: dict[int, str] = {}
_unauthorized_notice_times: OrderedDict[int, float] = OrderedDict()
_background_tasks: set[asyncio.Task[object]] = set()
_voice_catalog_results: dict[str, list[dict[str, object]]] = {}
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
    "drafting_script": "📝",
    "awaiting_script_approval": "🟣",
    "generating_assets": "🎨",
    "awaiting_approval": "🟠",
    "approved": "🟢",
    "rendering": "🔵",
    "published": "✅",
    "failed": "🔴",
    "cancelled": "⚫",
}

_STAGE_LABEL = {
    "topic": "Topic selection",
    "script": "Script review",
    "assets": "Media generation",
    "quality_check": "Quality review",
    "render": "Video render",
    "published": "Published",
}


# ── Button helpers ──────────────────────────────────────────────────────
def _button(text: str, callback_data: str) -> dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def _icon_button(slot: str, text: str, callback_data: str) -> dict[str, str]:
    return icon_button(slot, text, callback_data=callback_data)


def _link_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def _keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Premium mobile-first navigation grouped by operator workflow."""
    paused = is_paused()
    toggle = _icon_button("resume", "Resume factory", "resume") if paused else _icon_button("pause", "Pause factory", "pause")
    return {
        "inline_keyboard": [
            [_icon_button("studio", "Studio", "nav:studio"), _icon_button("review", "Review", "nav:review")],
            [_icon_button("analytics", "Insights", "nav:insights"), _icon_button("operations", "Operations", "nav:ops")],
            [_icon_button("settings", "Settings", "nav:settings"), _icon_button("automation", "Automation", "automation_panel")],
            [toggle, _icon_button("refresh", "Refresh", "menu")],
        ]
    }


def _studio_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .hq import get_genre_id, get_genre_mode

    return {"inline_keyboard": [
        [_button("✨ Create topic", "create"), _button("🧠 Custom series", "custom_creator")],
        [_button("📚 Story series", "series_panel"), _button("🎭 Format pack", "pack_panel")],
        [_button("🔥 Trending topics", "trends"), _button("🤖 Run automation", "automation:run")],
        [_button("🎭 Genre library", "genre:panel")],
        [_button(
            f"🎬 {get_genre_mode()} · {(get_genre_id() or 'auto')}",
            "genre:panel",
        )],
        [_button("🎞 Clipping studio", "clip_studio")],
        [_button("🔙 Home", "menu")],
    ]}


def _review_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": [
        [_icon_button("review_queue", "Review queue", "queue"), _icon_button("pipeline_status", "Pipeline status", "status")],
        [_icon_button("quality_review", "Clip review", "clip_studio")],
        [_icon_button("back", "Home", "menu")],
    ]}


def _insights_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": [
        [_icon_button("analytics", "Analytics", "analytics"), _icon_button("trends", "Trends", "trends")],
        [_icon_button("analytics_sync", "Sync YouTube", "sync_analytics")],
        [_icon_button("back", "Home", "menu")],
    ]}


def _operations_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    paused = is_paused()
    toggle = _icon_button("resume", "Resume factory", "resume") if paused else _icon_button("pause", "Pause factory", "pause")
    return {"inline_keyboard": [
        [_icon_button("system_health", "System health", "system_health"), _icon_button("pipeline_status", "Status", "status")],
        [_icon_button("review_queue", "Queue", "queue"), _icon_button("render_worker", "Render worker", "video_pipeline")],
        [_button("💳 Higgsfield credits", "credits_panel"), _button("📚 Story series", "series_panel")],
        [toggle, _icon_button("automation", "Automation", "automation_panel")],
        [_icon_button("back", "Home", "menu")],
    ]}


def _settings_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .hq import get_watermark_text, is_watermark_enabled

    return {"inline_keyboard": [
        [_button("🎯 Niche", "hq:niche"), _button("🎞 Format", "hq:format")],
        [_button("🧩 Providers", "providers"), _button("🎵 Audio", "audio_catalog")],
        [_button("🔗 Accounts", "accounts"), _button("📡 Destinations", "hq:publish_target")],
        [_button("📤 Daily limit", "hq:limit"), _button("🧪 Publishing mode", "publishing_mode")],
        [_button(
            f"🔒 Watermark: {'ON' if is_watermark_enabled() else 'OFF'}",
            "watermark:toggle",
        )],
        [_button(f"🔒 Text: {get_watermark_text()}", "watermark:text")],
        [_button("💎 Premium icons", "icon:panel:0")],
        [_button("🤖 Auto mode", "auto:panel")],
        [_button("⚙️ Advanced controls", "controls")],
        [_button("🔙 Home", "menu")],
    ]}


def _genre_message(category: str | None = None) -> str:
    from .genres import CATEGORY_LABELS, GENRE_CATEGORIES, genres_in_category
    from .hq import get_genre_id, get_genre_mode

    selected = get_genre_id()
    mode = get_genre_mode()
    lines = [
        "🎭 <b>Genre library</b>",
        _divider(),
        "",
        f"Mode: <b>{mode}</b>",
        f"Chosen: <b>{selected or 'auto match'}</b>",
        "",
        "A genre shapes the hook, the visual engine and the pacing. "
        "Pick one, shuffle inside a category, or draw at random.",
    ]
    if category:
        lines.extend(["", f"<b>{CATEGORY_LABELS.get(category, category)}</b>"])
        for genre in genres_in_category(category):
            mark = "✅ " if genre["id"] == selected else ""
            lines.append(f"{mark}{genre['label']}")
    else:
        lines.extend(["", "<b>Categories</b>"])
        for name in GENRE_CATEGORIES:
            lines.append(f"• {CATEGORY_LABELS.get(name, name)}")
    return "\n".join(lines)


def _genre_keyboard(category: str | None = None) -> dict[str, list[list[dict[str, str]]]]:
    from .genres import CATEGORY_LABELS, GENRE_CATEGORIES, genres_in_category
    from .hq import get_genre_id

    selected = get_genre_id()
    rows: list[list[dict[str, str]]] = []
    if category:
        for genre in genres_in_category(category):
            mark = "✅ " if genre["id"] == selected else ""
            rows.append([_button(f"{mark}{genre['label']}", f"genre_set:{genre['id']}")])
        rows.append([_button("🎲 Shuffle this category", f"genre_shuffle:{category}")])
    else:
        for name in GENRE_CATEGORIES:
            rows.append([_button(f"{CATEGORY_LABELS.get(name, name)}", f"genre_category:{name}")])
        rows.append([
            _button("🎲 Shuffle one", "genre_shuffle:all"),
            _button("🎰 Random one", "genre_random"),
        ])
    rows.append([_button("🔙 Back", "nav:studio" if category else "nav:settings")])
    return {"inline_keyboard": rows}


def _auto_message() -> str:
    from .auto_flow import recent_automation_events
    from .cost import spend_summary
    from .hq import (
        get_auto_publish_max_cost_usd,
        is_auto_clip_publish_enabled,
        is_auto_mode_enabled,
        is_auto_publish_enabled,
    )

    auto_mode = is_auto_mode_enabled()
    auto_publish = is_auto_publish_enabled()
    clip_publish = is_auto_clip_publish_enabled()
    spend = spend_summary(30)
    lines = [
        "🤖 <b>Automation &amp; auto mode</b>",
        _divider(),
        "",
        f"Auto build (script, media, render): <b>{'ON' if auto_mode else 'OFF'}</b>",
        f"Auto publish original videos: <b>{'ON' if auto_publish else 'OFF'}</b>",
        f"Auto publish clips: <b>{'ON' if clip_publish else 'OFF'}</b>",
        f"Per-video cost ceiling: <b>${get_auto_publish_max_cost_usd():.2f}</b>",
        f"Provider spend (30 days): <b>${float(spend.get('total_usd', 0.0)):.2f}</b> "
        f"across {spend.get('jobs', 0)} jobs",
        "",
        "Auto mode still respects the quality gate, the daily limit, the account "
        "binding and the pause switch. Turning the factory off stops everything "
        "immediately, and every automatic action is logged below.",
    ]
    events = recent_automation_events(8)
    if events:
        lines.extend(["", "<b>Recent automatic actions</b>"])
        lines.extend(f"<code>{html.escape(line)}</code>" for line in events)
    return "\n".join(lines)


def _auto_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .hq import (
        get_auto_publish_max_cost_usd,
        is_auto_clip_publish_enabled,
        is_auto_mode_enabled,
        is_auto_publish_enabled,
    )

    auto_mode = is_auto_mode_enabled()
    auto_publish = is_auto_publish_enabled()
    clip_publish = is_auto_clip_publish_enabled()
    rows = [
        [_button(
            "⏸️ Stop auto build" if auto_mode else "▶️ Start auto build",
            "auto:mode_off" if auto_mode else "auto:mode_on",
        )],
        [_button(
            "⏸️ Stop auto publish" if auto_publish else "🚀 Start auto publish",
            "auto:publish_off" if auto_publish else "auto:publish_on",
        )],
        [_button(
            "⏸️ Stop auto clip publish" if clip_publish else "🎞 Start auto clip publish",
            "auto:clip_off" if clip_publish else "auto:clip_on",
        )],
        [_button(f"💵 Ceiling ${get_auto_publish_max_cost_usd():.2f}", "auto:cost")],
        [_button("🔄 Refresh", "auto:panel")],
        [_button("🔙 Back", "nav:settings")],
    ]
    return {"inline_keyboard": rows}


def _navigation_message(section: str) -> str:
    copy = {
        "studio": ("✨", "Studio", "Create original content, custom series, trend drafts, and future clips."),
        "review": ("✅", "Review", "Review scripts before spend, then inspect rendered videos before publishing."),
        "insights": ("📈", "Insights", "Use trends and measured channel analytics to choose what to make next."),
        "ops": ("🖥", "Operations", "Monitor queues, workers, automation, failures, and factory state."),
        "settings": ("⚙️", "Settings", "Configure craft, providers, audio, accounts, destinations, and limits."),
    }
    icon, title, detail = copy[section]
    return f"{icon} <b>{title}</b>\n{_divider()}\n\n{detail}\n\nChoose an action below."


_ICON_PAGE_SIZE = 8


def _icon_panel_message(page: int) -> str:
    enabled, configured = get_icon_configuration()
    slots = list(ICON_SLOTS)
    pages = max(1, (len(slots) + _ICON_PAGE_SIZE - 1) // _ICON_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * _ICON_PAGE_SIZE
    lines = [
        "💎 <b>Premium Icons</b>",
        _divider(),
        "",
        f"State: <b>{'enabled' if enabled else 'Unicode fallback'}</b>",
        f"Configured: <b>{len(configured)}/{len(ICON_SLOTS)}</b>",
        f"Page: <b>{page + 1}/{pages}</b>",
        "",
        "Tap a semantic slot, then send its Telegram custom emoji or custom_emoji_id. "
        "Send /clear while setting a slot to remove it. Unicode remains the safe fallback.",
        "",
    ]
    for slot in slots[start:start + _ICON_PAGE_SIZE]:
        lines.append(
            f"{'✅' if slot in configured else '⚪'} {ICON_SLOTS[slot]} "
            f"<code>{html.escape(slot)}</code>"
        )
    return "\n".join(lines)


def _icon_panel_keyboard(page: int) -> dict[str, list[list[dict[str, str]]]]:
    enabled, configured = get_icon_configuration()
    slots = list(ICON_SLOTS)
    pages = max(1, (len(slots) + _ICON_PAGE_SIZE - 1) // _ICON_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * _ICON_PAGE_SIZE
    rows = [
        [_button(
            f"{'✓ ' if slot in configured else ''}{ICON_SLOTS[slot]} {slot.replace('_', ' ').title()}",
            f"icon:set:{slot}",
        )]
        for slot in slots[start:start + _ICON_PAGE_SIZE]
    ]
    navigation = []
    if page > 0:
        navigation.append(_button("◀ Previous", f"icon:panel:{page - 1}"))
    if page + 1 < pages:
        navigation.append(_button("Next ▶", f"icon:panel:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([_button(
        "Disable premium icons" if enabled else "Enable configured icons",
        "icon:disable" if enabled else "icon:enable",
    )])
    rows.append([_button("Test home keyboard", "icon:test")])
    rows.append([_icon_button("back", "Settings", "nav:settings")])
    return {"inline_keyboard": rows}


def _custom_emoji_id_from_message(message: dict[str, Any]) -> str:
    entities = message.get("entities") or []
    custom_ids = [
        entity.get("custom_emoji_id")
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("type") == "custom_emoji"
        and isinstance(entity.get("custom_emoji_id"), str)
    ]
    if len(custom_ids) == 1:
        return str(custom_ids[0])
    return str(message.get("text") or "").strip()


async def _validate_custom_emoji_id(custom_emoji_id: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/getCustomEmojiStickers"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json={"custom_emoji_ids": [custom_emoji_id]})
        response.raise_for_status()
    result = response.json().get("result")
    if not isinstance(result, list) or len(result) != 1:
        raise ValueError("Telegram did not recognize that custom emoji ID")


async def _handle_icon_input(chat_id: int, message: dict[str, Any]) -> None:
    slot = _pending_icon_slot[chat_id]
    text = str(message.get("text") or "").strip()
    if text == "/cancel":
        _pending_icon_slot.pop(chat_id, None)
        await send_message(chat_id, "↩️ Premium icon setup cancelled.", _icon_panel_keyboard(0))
        return
    if text == "/clear":
        clear_icon_id(slot)
        _pending_icon_slot.pop(chat_id, None)
        await send_message(chat_id, f"🗑 Cleared <code>{html.escape(slot)}</code>.", _icon_panel_keyboard(0))
        return
    custom_emoji_id = _custom_emoji_id_from_message(message)
    try:
        await _validate_custom_emoji_id(custom_emoji_id)
        set_icon_id(slot, custom_emoji_id)
        set_icons_enabled(True)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        await send_message(
            chat_id,
            f"⚠️ Could not validate that premium emoji ({html.escape(type(exc).__name__)}). "
            "Send one Telegram custom emoji, its custom_emoji_id, /clear, or /cancel.",
            _back_keyboard(),
        )
        return
    _pending_icon_slot.pop(chat_id, None)
    await send_message(
        chat_id,
        f"✅ Premium icon saved for <code>{html.escape(slot)}</code>.",
        _icon_panel_keyboard(0),
    )


def _clip_studio_message() -> str:
    jobs = list_clip_jobs(8)
    lines = [
        "🎞 <b>Clipping Studio</b>",
        _divider(),
        "",
        "Submit a public HTTPS video or live-stream URL. The isolated clip worker creates "
        "three vertical candidates for review; nothing publishes automatically.",
    ]
    if not jobs:
        lines.extend(["", "No clipping jobs yet."])
    else:
        lines.extend(["", "<b>Recent jobs</b>"])
        for job in jobs:
            lines.append(
                f"• <code>{job.id[:8]}</code> · {html.escape(job.source_kind.upper())} · "
                f"<b>{html.escape(job.status.replace('_', ' ').title())}</b> · "
                f"{html.escape(job.source_display[:80])}"
            )
    return "\n".join(lines)


def _clip_studio_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    rows = [
        [_icon_button("clip_vod", "Clip a video URL", "clip_new:vod")],
        [_icon_button("clip_live", "Capture live · 5 min", "clip_new:live:300"), _icon_button("clip_live", "15 min", "clip_new:live:900")],
    ]
    for job in list_clip_jobs(6):
        rows.append([_button(
            f"{'✅' if job.status == 'awaiting_review' else '⏳'} {job.id[:8]} · {job.status.replace('_', ' ')}",
            f"clip_job:{job.id}",
        )])
    rows.extend([
        [_icon_button("refresh", "Refresh", "clip_studio")],
        [_icon_button("back", "Studio", "nav:studio")],
    ])
    return {"inline_keyboard": rows}


def _clip_job_message(job_id: str) -> str:
    job = next((item for item in list_clip_jobs(50) if item.id == job_id), None)
    if job is None:
        raise ValueError("Clip job not found")
    candidates = list_clip_candidates(job.id)
    lines = [
        f"🎞 <b>Clip job {job.id[:8]}</b>",
        _divider(),
        "",
        f"Source: {html.escape(job.source_display)}",
        f"Mode: <b>{html.escape(job.source_kind.upper())}</b>",
        f"Status: <b>{html.escape(job.status.replace('_', ' ').title())}</b>",
        f"Stage: {html.escape(job.stage)}",
    ]
    if job.error_message:
        lines.append(f"Error: {html.escape(job.error_message)}")
    for candidate in candidates:
        seconds = (candidate.end_ms - candidate.start_ms) / 1000
        lines.extend([
            "",
            f"<b>#{candidate.rank} · score {candidate.score}/1000 · {seconds:.1f}s</b>",
            html.escape(candidate.title),
            f"Review: {html.escape(candidate.status.replace('_', ' '))}",
        ])
    return "\n".join(lines)


def _clip_job_keyboard(job_id: str) -> dict[str, list[list[dict[str, str]]]]:
    job = next((item for item in list_clip_jobs(50) if item.id == job_id), None)
    rows = []
    if job is not None and job.status == "failed":
        rows.append([_button("🔁 Retry clip job", f"clip_retry:{job_id}")])
    for candidate in list_clip_candidates(job_id):
        rows.append([_button(f"▶️ Preview #{candidate.rank}", f"clip_preview:{candidate.id}")])
        if candidate.status == "awaiting_review":
            rows.append([
                _button("✅ Approve", f"clip_review:approved:{candidate.id}"),
                _button("❌ Reject", f"clip_review:rejected:{candidate.id}"),
            ])
        elif candidate.status == "approved":
            rows.append([_button("📤 Send to publish queue", f"clip_promote:{candidate.id}")])
    rows.extend([
        [_button("🔄 Refresh", f"clip_job:{job_id}")],
        [_button("🔙 Clipping Studio", "clip_studio")],
    ])
    return {"inline_keyboard": rows}


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
            [_button("🎵 Audio library", "audio_catalog")],
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
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


def _job_keyboard(job_or_id: Any) -> dict[str, list[list[dict[str, str]]]]:
    """Show only actions that are valid for the current job state."""
    job = job_or_id if not isinstance(job_or_id, str) else None
    job_id = str(getattr(job, "id", job_or_id))
    if job is None and isinstance(job_or_id, str):
        from .db import ContentJob, SessionLocal

        with SessionLocal() as session:
            job = session.get(ContentJob, job_or_id)

    rows: list[list[dict[str, str]]] = []
    if job is None:
        rows.extend([
            [_button("✅ Approve", f"approve:{job_id}"), _button("✍️ Revise", f"revise:{job_id}")],
            [_button("📤 Publish now", f"publish:{job_id}"), _button("🔍 Quality check", f"quality:{job_id}")],
            [_button("▶️ Preview video", f"preview:{job_id}")],
        ])
    elif job.status == "awaiting_script_approval":
        rows.extend([
            [_button("✅ Approve script & generate", f"script_approve:{job_id}:{job.content_version}")],
            [_button("📝 Use my own script", f"script_custom:{job_id}:{job.content_version}")],
            [_button("🔄 Rewrite script", f"script_regenerate:{job_id}:{job.content_version}")],
            [_button("🗑 Reject job", f"script_reject:{job_id}:{job.content_version}")],
        ])
        variants = _hook_variants_for(job)
        if variants:
            rows.insert(
                1,
                [
                    _button(f"🪝 Hook {position}", f"hook:{job_id}:{position - 1}")
                    for position in range(1, len(variants) + 1)
                ],
            )
    elif job.status in {"drafting_script", "generating_assets", "queued", "rendering"}:
        if job.status not in {"rendering", "generating_assets"}:
            rows.append([_button("🗑 Cancel job", f"cancel:{job_id}")])
        rows.append([_button("📥 Refresh queue", "queue")])
    elif job.status == "awaiting_approval":
        rows.extend([
            [_button("✅ Approve final video", f"approve:{job_id}:{job.content_version}"), _button("✍️ Revise", f"revise:{job_id}")],
            [_button("🔍 Quality check", f"quality:{job_id}"), _button("▶️ Preview video", f"preview:{job_id}")],
            [_button("🎨 My own cover", f"cover_custom:{job_id}:{job.content_version}")],
            [_button("🗑 Cancel job", f"cancel:{job_id}")],
        ])
    elif job.status == "approved":
        rows.extend([
            [_button("📤 Publish now", f"publish:{job_id}"), _button("⏰ Schedule reminder", f"schedule:{job_id}")],
            [_button("▶️ Preview video", f"preview:{job_id}"), _button("✍️ Revise", f"revise:{job_id}")],
        ])
    elif job.status == "failed":
        rows.append([_button("🔁 Retry", f"retry:{job_id}"), _button("🗑 Cancel job", f"cancel:{job_id}")])
    rows.append([_button("🔙 Back to menu", "menu")])
    return {"inline_keyboard": rows}


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
    "drafting_script": (10, "10% · Drafting script"),
    "awaiting_script_approval": (20, "20% · Script review"),
    "generating_assets": (35, "35% · Generating media"),
    "queued": (20, "20% · Queued"),
    "rendering": (50, "50% · Rendering video"),
    "awaiting_approval": (75, "75% · Ready for review"),
    "approved": (90, "90% · Approved & ready to publish"),
    "published": (100, "100% · Published"),
    "failed": (0, "Failed"),
    "cancelled": (0, "Cancelled"),
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


async def _post_telegram(url: str, payload: dict[str, Any] | None = None, files: Any = None) -> httpx.Response:
    """POST to Telegram with a short retry so a flaky network never loses a message."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if files is None:
                    response = await client.post(url, json=payload)
                else:
                    response = await client.post(url, data=payload, files=files)
        except httpx.TransportError as exc:
            last_error = exc
            await asyncio.sleep(0.5 * (2**attempt))
            continue
        if response.status_code >= 500 and attempt < 2:
            await asyncio.sleep(0.5 * (2**attempt))
            continue
        return response
    raise last_error or RuntimeError("Telegram request failed")


async def send_message(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = await _post_telegram(url, _message(chat_id, text, reply_markup))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        if response.status_code != 400 or not has_custom_icons(reply_markup):
            raise
        fallback = unicode_fallback_markup(reply_markup)
        response = await _post_telegram(url, _message(chat_id, text, fallback))
        response.raise_for_status()


async def send_progress_message(chat_id: int, text: str) -> int | None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    response = await _post_telegram(url, _message(chat_id, text))
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
    response = await _post_telegram(url, payload)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        if response.status_code != 400 or not has_custom_icons(reply_markup):
            raise
        payload["reply_markup"] = unicode_fallback_markup(reply_markup)
        response = await _post_telegram(url, payload)
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


async def send_audio(chat_id: int, audio_path: Path, caption: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    if audio_path.stat().st_size > 49 * 1024 * 1024:
        raise ValueError("Audio preview exceeds Telegram's 49 MB preview limit.")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendAudio"
    with audio_path.open("rb") as audio:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": caption[:1000]},
                files={"audio": (audio_path.name, audio, "audio/mpeg")},
            )
            response.raise_for_status()


def _track_background_task(task: asyncio.Task[object]) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_voice_benchmark_and_send(chat_id: int) -> None:
    from .voice_benchmark import run_voice_benchmark

    try:
        result = await asyncio.to_thread(
            run_voice_benchmark,
            Path(get_settings().media_root),
        )
        output_dir = Path(str(result["output_dir"]))
        samples = result["samples"]
        if not isinstance(samples, list):
            raise RuntimeError("Voice benchmark returned invalid results.")
        for sample in samples:
            if not isinstance(sample, dict) or sample.get("status") != "ready":
                continue
            label = str(sample["label"])
            await send_audio(
                chat_id,
                output_dir / str(sample["filename"]),
                f"Blind voice sample {label}",
            )
        failed = [
            str(sample.get("label"))
            for sample in samples
            if isinstance(sample, dict) and sample.get("status") != "ready"
        ]
        note = f"\nFailed samples: {', '.join(failed)}" if failed else ""
        await send_message(
            chat_id,
            "🎧 <b>Blind voice benchmark ready</b>\n\n"
            "Listen on headphones and compare Hindi naturalness, emotion, pronunciation, "
            f"and trust. Provider names remain hidden until reveal.{note}",
            {"inline_keyboard": [
                [_button(
                    "Reveal providers",
                    f"voice_benchmark_reveal:{result['benchmark_id']}",
                )],
                [_button("🔙 Back to Voice", "provider_role:voice")],
            ]},
        )
    except Exception as exc:
        logger.exception("Voice benchmark failed")
        await send_message(
            chat_id,
            f"⚠️ Voice benchmark failed ({html.escape(type(exc).__name__)}). "
            "Check provider credentials, voice IDs, credits, and no-paid mode.",
            _provider_role_keyboard("voice"),
        )


def _hook_variants_for(job: Any) -> list[str]:
    """Hook options offered for a script under review."""
    try:
        import json as _json

        from .hooks import hook_variants

        metadata = _json.loads(getattr(job, "metadata_json", None) or "{}")
    except (TypeError, ValueError):
        return []
    return hook_variants(metadata)


def _script_review_message(job: Any) -> str:
    script = html.escape((job.script_text or "").strip())
    if len(script) > 3500:
        script = script[:3497] + "..."
    try:
        import json as _json

        metadata = _json.loads(job.metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    polish_note = (
        "\n✨ <i>Retention editor pass applied.</i>\n" if metadata.get("script_polished") else ""
    )
    custom_note = (
        "\n📝 <i>This is your own script.</i>\n" if metadata.get("custom_script") else ""
    )
    variants = _hook_variants_for(job)
    hook_note = ""
    if variants:
        selected = str(metadata.get("selected_hook") or "")
        lines = []
        for position, variant in enumerate(variants, start=1):
            mark = "✅" if selected and selected == variant else f"{position}️⃣"
            lines.append(f"  {mark} <i>{html.escape(variant[:120])}</i>")
        hook_note = (
            "\n🪝 <b>Hook options</b> (tap one to swap the opening):\n"
            + "\n".join(lines)
            + "\n"
        )
    return (
        f"📝 <b>Script review · v{job.content_version}</b>\n{_divider()}\n\n"
        f"<b>{html.escape(job.topic)}</b>\n\n"
        f"{script}\n\n"
        f"{hook_note}{polish_note}{custom_note}"
        "No voice, image, video, music, or rendering credits have been spent yet. "
        "Approve this exact version to start media generation.\n"
        f"{_divider()}"
    )


class ProgressBoard:
    """A live, editable Telegram progress screen for long factory work."""

    def __init__(self, chat_id: int, title: str, steps: list[str]) -> None:
        self.chat_id = chat_id
        self.title = title
        self.steps = list(steps)
        self.done: set[str] = set()
        self.message_id: int | None = None

    def _text(self) -> str:
        lines = [f"⏳ <b>{self.title}</b>", _divider(), ""]
        for step in self.steps:
            mark = "✅" if step in self.done else "▫️"
            lines.append(f"{mark} {step}")
        lines.append("")
        lines.append("This screen updates live. Do not close it.")
        return "\n".join(lines)

    async def start(self) -> None:
        self.message_id = await send_progress_message(self.chat_id, self._text())

    async def update(self, step: str) -> None:
        if step in self.done:
            return
        self.done.add(step)
        text = self._text()
        if self.message_id is not None:
            await finish_progress_message(self.chat_id, self.message_id, text)
        else:
            self.message_id = await send_progress_message(self.chat_id, text)

    async def finish(self, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        if self.message_id is not None:
            await finish_progress_message(self.chat_id, self.message_id, text, reply_markup)
        else:
            await send_message(self.chat_id, text, reply_markup)


async def _draft_script_with_progress(chat_id: int, job_id: str) -> None:
    """Draft a script in the background behind a live progress screen."""
    board = ProgressBoard(
        chat_id,
        "✨ Writing your script",
        ["Angle & hook research", "Retention draft", "Editor polish", "Titles, hashtags & cover"],
    )
    await board.start()
    try:
        job = await asyncio.to_thread(generate_content, job_id)
    except Exception as exc:
        logger.exception("Script drafting failed")
        await board.finish(
            f"⚠️ Script drafting failed ({html.escape(type(exc).__name__)}). "
            "Check the script provider status, then create the job again.",
            _keyboard(),
        )
        return
    try:
        import json as _json

        metadata = _json.loads(job.metadata_json or "{}")
        if metadata.get("script_polished"):
            await board.update("Editor polish")
        await board.update("Titles, hashtags & cover")
    except (TypeError, ValueError):
        pass
    if job.status != "awaiting_script_approval":
        await board.finish(
            "🤖 <b>Auto mode took over</b>\n"
            "The script was approved, media was generated, and the job is queued for the "
            "render worker. Nothing is published unless auto publish is enabled.\n\n"
            f"{_format_job_card(job)}",
            _job_keyboard(job),
        )
        return
    await board.finish(_script_review_message(job), _job_keyboard(job))


async def _generate_assets_and_notify(chat_id: int, job_id: str, version: int) -> None:
    board = ProgressBoard(
        chat_id,
        "🎨 Generating media",
        ["Narration (voice)", "Scene artwork", "AI video beats", "Cover image", "Music mix"],
    )
    await board.start()
    try:
        job = await asyncio.to_thread(generate_assets, job_id, version)
        await board.finish(
            "🎨 <b>Media package generated</b>\n"
            "The job is queued for the Railway render worker.\n\n"
            f"{_format_job_card(job)}",
            _job_keyboard(job),
        )
    except Exception as exc:
        logger.exception("Asset generation failed")
        await board.finish(
            f"⚠️ Asset generation failed ({html.escape(type(exc).__name__)}). "
            "No automatic retry was started. Review provider status, then use Retry.",
            _keyboard(),
        )


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


def _audio_catalog_message() -> str:
    from .audio_catalog import get_default_audio_asset, list_audio_assets

    media_root = Path(get_settings().media_root)
    entries = list_audio_assets(media_root)
    default_bgm = get_default_audio_asset(media_root, "bgm")
    default_sfx = get_default_audio_asset(media_root, "sfx")
    lines = [
        "🎵 <b>Audio library</b>",
        _divider(),
        "",
        "Upload operator-approved MP3, WAV, OGG, or M4A files (maximum 20 MB). ",
        "The selected BGM is attached to new jobs, ducked under narration, and never exposed publicly.",
        "",
        f"BGM tracks: <b>{sum(entry.get('kind') == 'bgm' for entry in entries)}</b>",
        f"SFX files: <b>{sum(entry.get('kind') == 'sfx' for entry in entries)}</b>",
        f"Default BGM: <b>{html.escape(str((default_bgm or {}).get('name') or 'none'))}</b>",
        f"Default SFX: <b>{html.escape(str((default_sfx or {}).get('name') or 'none'))}</b>",
    ]
    if entries:
        lines.extend(["", "<b>Catalog</b>"])
        for entry in entries[:10]:
            lines.append(
                f"• {html.escape(str(entry.get('kind', '')).upper())} · "
                f"{html.escape(str(entry.get('name') or entry.get('id'))[:80])}"
            )
    return "\n".join(lines)


def _audio_catalog_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .audio_catalog import get_default_audio_asset, list_audio_assets

    media_root = Path(get_settings().media_root)
    entries = list_audio_assets(media_root)
    defaults = {
        kind: (get_default_audio_asset(media_root, kind) or {}).get("id")
        for kind in ("bgm", "sfx")
    }
    rows = [[
        _button("➕ Add BGM", "audio_add:bgm"),
        _button("➕ Add SFX", "audio_add:sfx"),
    ]]
    for entry in entries[:10]:
        asset_id = str(entry.get("id") or "")
        kind = str(entry.get("kind") or "")
        if not asset_id or kind not in {"bgm", "sfx"}:
            continue
        selected = "✓ " if defaults.get(kind) == asset_id else ""
        label = str(entry.get("name") or asset_id)
        rows.append([
            _button(f"{selected}{kind.upper()} · {label[:28]}", f"audio_default:{kind}:{asset_id}"),
            _button("🗑", f"audio_delete:{asset_id}"),
        ])
    rows.append([_icon_button("back", "Settings", "nav:settings")])
    return {"inline_keyboard": rows}


async def _download_telegram_audio(file_id: str, declared_size: int | None) -> bytes:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    if declared_size is not None and declared_size > 20 * 1024 * 1024:
        raise ValueError("Audio upload exceeds the 20 MB limit.")
    async with httpx.AsyncClient(timeout=90) as client:
        metadata = await client.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile",
            params={"file_id": file_id},
        )
        metadata.raise_for_status()
        file_path = (metadata.json().get("result") or {}).get("file_path")
        if (
            not isinstance(file_path, str)
            or ".." in file_path
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", file_path)
        ):
            raise RuntimeError("Telegram returned an invalid audio path.")
        response = await client.get(
            f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
        )
        response.raise_for_status()
        if len(response.content) > 20 * 1024 * 1024:
            raise ValueError("Audio upload exceeds the 20 MB limit.")
        return response.content


async def _handle_audio_upload(chat_id: int, message: dict[str, Any]) -> None:
    kind = _pending_audio_upload.get(chat_id)
    text = str(message.get("text") or "").strip()
    if text == "/cancel":
        _pending_audio_upload.pop(chat_id, None)
        await send_message(chat_id, "↩️ Audio upload cancelled.", _audio_catalog_keyboard())
        return
    media = message.get("audio") or message.get("document")
    if not isinstance(media, dict) or not isinstance(media.get("file_id"), str):
        await send_message(
            chat_id,
            "⚠️ Send an MP3, WAV, OGG, or M4A audio file, or /cancel.",
            _back_keyboard(),
        )
        return
    original_name = str(media.get("file_name") or f"telegram-{kind}.mp3")
    declared_size = media.get("file_size") if isinstance(media.get("file_size"), int) else None
    data = await _download_telegram_audio(str(media["file_id"]), declared_size)
    from .audio_catalog import register_audio_asset

    entry = register_audio_asset(
        Path(get_settings().media_root),
        kind=str(kind),
        original_name=original_name,
        data=data,
    )
    _pending_audio_upload.pop(chat_id, None)
    await send_message(
        chat_id,
        f"✅ <b>{html.escape(str(kind).upper())} added</b>\n"
        f"{html.escape(str(entry['name']))}\n\n"
        "The first file of each type becomes the default; select another below at any time.",
        _audio_catalog_keyboard(),
    )


async def _download_telegram_document(file_id: str, max_bytes: int) -> bytes:
    """Download a Telegram file after validating its declared size."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    async with httpx.AsyncClient(timeout=120) as client:
        metadata = await client.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getFile",
            params={"file_id": file_id},
        )
        metadata.raise_for_status()
        payload = metadata.json().get("result") or {}
        file_path = payload.get("file_path")
        file_size = payload.get("file_size")
        if isinstance(file_size, int) and file_size > max_bytes:
            raise ValueError("The uploaded file is too large.")
        if (
            not isinstance(file_path, str)
            or ".." in file_path
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", file_path)
        ):
            raise RuntimeError("Telegram returned an invalid file path.")
        response = await client.get(
            f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
        )
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise ValueError("The uploaded file is too large.")
        return response.content


async def _handle_custom_script(chat_id: int, message: dict[str, Any]) -> None:
    job_id, version = _pending_custom_script[chat_id]
    text = str(message.get("text") or "").strip()
    if text == "/cancel":
        _pending_custom_script.pop(chat_id, None)
        await send_message(chat_id, "↩️ Custom script cancelled.", _keyboard())
        return
    from .owner_inputs import apply_custom_script

    try:
        job = apply_custom_script(job_id, version, text)
    except ValueError as exc:
        await send_message(
            chat_id,
            f"⚠️ {html.escape(str(exc))}\nSend the full script or /cancel.",
            _back_keyboard(),
        )
        return
    _pending_custom_script.pop(chat_id, None)
    await send_message(
        chat_id,
        _script_review_message(job),
        _job_keyboard(job),
    )


async def _handle_custom_voice(chat_id: int, message: dict[str, Any]) -> None:
    job_id, version = _pending_custom_voice[chat_id]
    media = message.get("voice") or message.get("audio") or message.get("document")
    if not isinstance(media, dict) or not isinstance(media.get("file_id"), str):
        await send_message(
            chat_id,
            "⚠️ Send an MP3/M4A voice recording, or /cancel.",
            _back_keyboard(),
        )
        return
    from .owner_inputs import MAX_VOICE_BYTES, apply_custom_voiceover, save_custom_voice

    try:
        data = await _download_telegram_document(str(media["file_id"]), MAX_VOICE_BYTES)
        filename = save_custom_voice(Path(get_settings().media_root), job_id, data)
        job = apply_custom_voiceover(job_id, version, filename)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        await send_message(
            chat_id,
            f"⚠️ Voice-over not saved ({html.escape(type(exc).__name__)}). "
            "Send an MP3/M4A file under 25 MB, or /cancel.",
            _back_keyboard(),
        )
        return
    _pending_custom_voice.pop(chat_id, None)
    await send_message(
        chat_id,
        "🎙 <b>Your voice-over is attached.</b>\n"
        "It replaces provider narration for this job when media generation runs.",
        _job_keyboard(job),
    )


async def _handle_custom_cover(chat_id: int, message: dict[str, Any]) -> None:
    job_id, version, platform = _pending_custom_cover[chat_id]
    media = message.get("photo") or message.get("document")
    if not isinstance(media, dict) or not isinstance(media.get("file_id"), str):
        await send_message(
            chat_id,
            "⚠️ Send a JPEG image for the cover, or /cancel.",
            _back_keyboard(),
        )
        return
    from .owner_inputs import MAX_COVER_BYTES, apply_custom_cover, save_custom_cover

    try:
        data = await _download_telegram_document(str(media["file_id"]), MAX_COVER_BYTES)
        filename = save_custom_cover(Path(get_settings().media_root), job_id, platform, data)
        job = apply_custom_cover(job_id, platform, filename)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        await send_message(
            chat_id,
            f"⚠️ Cover not saved ({html.escape(type(exc).__name__)}). "
            "Send a JPEG under 12 MB, or /cancel.",
            _back_keyboard(),
        )
        return
    _pending_custom_cover.pop(chat_id, None)
    await send_message(
        chat_id,
        f"🎨 <b>Your {html.escape(platform.title())} cover is set.</b>\n"
        "It replaces the generated cover when this video is published.",
        _job_keyboard(job),
    )


async def _handle_clip_source(chat_id: int, message: dict[str, Any], update: dict[str, Any]) -> None:
    text = str(message.get("text") or "").strip()
    if text == "/cancel":
        _pending_clip_source.pop(chat_id, None)
        await send_message(chat_id, "↩️ Clip submission cancelled.", _clip_studio_keyboard())
        return
    source_kind, capture_seconds = _pending_clip_source[chat_id]
    update_id = update.get("update_id")
    request_id = f"telegram:{update_id}" if isinstance(update_id, int) else None
    try:
        job = create_clip_job(
            text,
            source_kind,
            capture_seconds=capture_seconds,
            request_id=request_id,
        )
    except (RuntimeError, ValueError) as exc:
        await send_message(
            chat_id,
            f"⚠️ {html.escape(str(exc))}\nSend a public HTTPS URL or /cancel.",
            _back_keyboard(),
        )
        return
    _pending_clip_source.pop(chat_id, None)
    await send_message(
        chat_id,
        f"✅ <b>Clip job queued</b>\n\n"
        f"ID: <code>{job.id}</code>\n"
        f"Mode: <b>{html.escape(job.source_kind.upper())}</b>\n"
        "The isolated clip worker will prepare three review candidates.",
        _clip_studio_keyboard(),
    )


async def _send_clip_candidate_preview(chat_id: int, candidate_id: str) -> None:
    from .db import ClipCandidate, SessionLocal

    with SessionLocal() as session:
        candidate = session.get(ClipCandidate, candidate_id)
        if candidate is None:
            raise ValueError("Clip candidate not found")
        filename = candidate.render_filename
        title = candidate.title
        rank = candidate.rank
    media_root = Path(get_settings().media_root).resolve()
    path = (media_root / filename).resolve()
    if path.parent != media_root or not path.is_file():
        raise ValueError("Clip candidate media is unavailable")
    await send_video(chat_id, path, f"Clip #{rank} · {title}"[:1000])


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
            ("video", "🎬 AI video"),
        )
    ]
    rows.append([_button(
        f"🛠 Render worker: {get_render_worker_target()}",
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
    rows.append([_icon_button("back", "Settings", "nav:settings")])
    return {"inline_keyboard": rows}


def _provider_role_keyboard(role: str) -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    current = get_provider_for_role(role)
    rows = []
    for provider, label in PROVIDER_ROLES[role].items():
        ready = (
            " ✅"
            if provider in {"google", "openai", "gemini", "huggingface", "elevenlabs", "fish_audio"}
            and status.get(provider)
            else " ✅"
            if provider == "minimax"
            and (status.get("minimax_mplan") if role == "script" else status.get("minimax"))
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
        if provider == "elevenlabs" and role == "voice":
            ready = " ✅" if status.get("elevenlabs") and status.get("elevenlabs_voice_id") else ""
        if provider == "fish_audio" and role == "voice":
            ready = " ✅" if status.get("fish_audio") and status.get("fish_audio_reference_id") else ""
        if provider == "minimax" and role == "voice":
            ready = " ✅" if status.get("minimax") and status.get("minimax_voice_id") else ""
        if provider == "minimax" and role == "video":
            ready = " ✅" if status.get("minimax") else ""
        if provider == "higgsfield" and role == "video":
            ready = " ✅" if status.get("higgsfield") else ""
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
        rows.append([_button("🎭 Browse Hindi voices", "voice_catalog")])
        rows.append([_button(
            "🎙️ Set ElevenLabs voice ID"
            + (" ✅" if status.get("elevenlabs_voice_id") else " ⚪"),
            "elevenlabs_voice_id",
        )])
        rows.append([_button(
            f"🎙 ElevenLabs model: {get_elevenlabs_tts_model()}",
            "elevenlabs_tts_model",
        )])
        rows.append([_button(
            "🐟 Set Fish Audio reference voice"
            + (" ✅" if status.get("fish_audio_reference_id") else " ⚪"),
            "fish_audio_reference_id",
        )])
        rows.append([_button(
            f"🐟 Fish model: {get_fish_audio_model_id()}",
            "fish_audio_model",
        )])
        rows.append([_button(
            "🎚 Set MiniMax voice ID"
            + (" ✅" if status.get("minimax_voice_id") else " ⚪"),
            "minimax_voice_id",
        )])
        rows.append([_button(
            f"🎚 MiniMax model: {get_minimax_speech_model()}",
            "minimax_speech_model",
        )])
        rows.append([_button("🎧 Blind voice benchmark", "voice_benchmark")])
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
            "ElevenLabs, Fish Audio, and MiniMax speech can consume API or plan credits. "
            "Use Browse Hindi voices to select provider-owned/system or account voices explicitly. "
            if role == "voice"
            else ""
        )
        + (
            "MiniMax and Higgsfield generate selected high-impact scene videos; FFmpeg remains the final renderer. "
            if role == "video"
            else ""
        )
        + "Repeatedly limited providers are temporarily skipped. "
        "If OpenAI scriptwriting is unavailable, the built-in Hindi template is used instead. "
        "Choices apply to new jobs only."
    )


def _fish_audio_model_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_fish_audio_model_id()
    rows = [
        [_button(
            f"{'✓ ' if model == current else ''}{model}",
            f"fish_audio_model_set:{model}",
        )]
        for model in FISH_AUDIO_MODELS
    ]
    rows.append([_button("🔙 Back to Voice", "provider_role:voice")])
    return {"inline_keyboard": rows}


def _elevenlabs_tts_model_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_elevenlabs_tts_model()
    rows = [
        [_button(
            f"{'✓ ' if model == current else ''}{model}",
            f"elevenlabs_tts_model_set:{model}",
        )]
        for model in ELEVENLABS_TTS_MODELS
    ]
    rows.append([_button("🔙 Back to Voice", "provider_role:voice")])
    return {"inline_keyboard": rows}


def _minimax_speech_model_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    current = get_minimax_speech_model()
    rows = [
        [_button(
            f"{'✓ ' if model == current else ''}{model}",
            f"minimax_speech_model_set:{model}",
        )]
        for model in MINIMAX_SPEECH_MODELS
    ]
    rows.append([_button("🔙 Back to Voice", "provider_role:voice")])
    return {"inline_keyboard": rows}


_VOICE_CATALOG_PAGE_SIZE = 8


def _voice_catalog_home_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    status = provider_status()
    return {"inline_keyboard": [
        [_button(
            f"🐟 Fish Official Hindi{' ✅' if status.get('fish_audio') else ''}",
            "voice_catalog:fish_audio:0",
        )],
        [_button(
            f"🎚 MiniMax Hindi system{' ✅' if status.get('minimax') else ''}",
            "voice_catalog:minimax:0",
        )],
        [_button(
            f"🎙 ElevenLabs account Hindi{' ✅' if status.get('elevenlabs') else ''}",
            "voice_catalog:elevenlabs:0",
        )],
        [_button("🔙 Back to Voice", "provider_role:voice")],
    ]}


def _cache_voice_options(provider: str, options: list[dict[str, object]]) -> None:
    _voice_catalog_results[provider] = options[:100]


def _voice_token(provider: str, voice_id: str) -> str:
    return hashlib.sha256(f"{provider}:{voice_id}".encode("utf-8")).hexdigest()[:16]


def _voice_from_token(provider: str, token: str) -> dict[str, object]:
    for option in _voice_catalog_results.get(provider, []):
        voice_id = str(option.get("voice_id") or "")
        if voice_id and _voice_token(provider, voice_id) == token:
            return option
    raise ValueError("Voice catalog selection expired; refresh the catalog")


def _voice_catalog_message(provider: str, page: int) -> str:
    labels = {
        "fish_audio": "Fish Official Hindi voices",
        "minimax": "MiniMax Hindi system voices",
        "elevenlabs": "ElevenLabs Hindi account voices",
    }
    options = _voice_catalog_results.get(provider, [])
    pages = max(1, (len(options) + _VOICE_CATALOG_PAGE_SIZE - 1) // _VOICE_CATALOG_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    lines = [
        f"🎭 <b>{labels.get(provider, provider)}</b>",
        _divider(),
        "",
        f"Found: <b>{len(options)}</b> · Page <b>{page + 1}/{pages}</b>",
        "Selection changes new generations only. Run Blind Voice Benchmark before production.",
        "",
    ]
    for option in options[page * _VOICE_CATALOG_PAGE_SIZE:(page + 1) * _VOICE_CATALOG_PAGE_SIZE]:
        source = html.escape(str(option.get("source") or ""))
        lines.append(
            f"• <b>{html.escape(str(option.get('name') or 'Voice'))}</b> · {source}\n"
            f"  {html.escape(str(option.get('description') or '')[:180])}"
        )
    if not options:
        lines.append("No eligible Hindi voices were returned for this account.")
    return "\n".join(lines)


def _voice_catalog_keyboard(provider: str, page: int) -> dict[str, list[list[dict[str, str]]]]:
    options = _voice_catalog_results.get(provider, [])
    pages = max(1, (len(options) + _VOICE_CATALOG_PAGE_SIZE - 1) // _VOICE_CATALOG_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = []
    for option in options[page * _VOICE_CATALOG_PAGE_SIZE:(page + 1) * _VOICE_CATALOG_PAGE_SIZE]:
        voice_id = str(option.get("voice_id") or "")
        if voice_id:
            rows.append([_button(
                f"Select · {str(option.get('name') or 'Voice')[:40]}",
                f"voice_choose:{provider}:{_voice_token(provider, voice_id)}",
            )])
    navigation = []
    if page > 0:
        navigation.append(_button("◀ Previous", f"voice_catalog:{provider}:{page - 1}"))
    if page + 1 < pages:
        navigation.append(_button("Next ▶", f"voice_catalog:{provider}:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.extend([
        [_button("🔄 Refresh catalog", f"voice_catalog_refresh:{provider}:0")],
        [_button("🔙 Voice catalogs", "voice_catalog")],
    ])
    return {"inline_keyboard": rows}


def _provider_video_message() -> str:
    railway_online = any(
        worker["worker_id"] == "railway-renderer" and worker["online"]
        for worker in worker_status()
    )
    worker_status_text = "online" if railway_online else "offline"
    return (
        "🎞 <b>Video rendering</b>\n"
        f"{_divider()}\n\n"
        "MiniMax/Higgsfield can generate selected source scenes; FFmpeg assembles, "
        "captions, mixes, validates, and exports the final video.\n\n"
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
        "Gemini is connected for scriptwriting; Edge TTS, ElevenLabs, Fish Audio, and MiniMax are connected for narration. "
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
            ("video", "🎬 AI video"),
        )
    ]
    return (
        f"🧩 <b>AI &amp; media settings</b>\n{_divider()}\n\n"
        + "\n".join(role_lines)
        + "\n  🛠 Renderer: <b>FFmpeg Railway worker</b>\n\n"
        f"AI media generation: <b>{'enabled' if media_generation_enabled() else 'disabled'}</b>\n"
        f"No-paid providers: <b>{'ON' if no_paid_providers_enabled() else 'OFF'}</b>\n"
        f"Default provider: <b>{html.escape(str(PROVIDER_INFO.get(str(status['selected']), {}).get('label', status['selected'])))}</b>\n\n"
        "Set script, image, narration, and AI-video providers independently below. "
        "AI video supplies selected scenes; final rendering always runs on the FFmpeg worker. "
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
    if configured:
        rows.append([_button("🗑 Remove stored key", f"provider_remove:{name}")])
    rows.append([_button("🔙 All providers", "provider_credentials")])
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
        {"command": "health", "description": "🩺 Check pipeline health"},
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
        f"🎬 <b>Content Factory Studio</b>\n"
        f"{_divider()}\n"
        f"  Factory: <b>{factory_state}</b>\n"
        f"  YouTube: {youtube_state}\n"
        f"  Mode: {'Safe preview (mock)' if is_mock_publishing() else 'Live'}\n"
        f"  Workflow: 📝 Script Approval → 🎨 Media → ✅ Final Approval\n"
        f"\n"
        f"Choose a workspace below, or type /help for commands.\n"
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
    active_jobs = [
        job
        for job in jobs
        if job.status in {
            "queued",
            "drafting_script",
            "awaiting_script_approval",
            "generating_assets",
            "rendering",
            "awaiting_approval",
        }
    ]
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


def _build_health_message() -> str:
    from .health import pipeline_health

    try:
        health = pipeline_health()
    except Exception as exc:
        return (
            "🩺 <b>Pipeline Health</b>\n"
            f"{_divider()}\n\n"
            f"  Health check failed: {_safe_error_text(exc)}"
        )

    status = str(health.get("status") or "unknown")
    icons = {"healthy": "🟢", "degraded": "🟠", "paused": "🔴"}
    workers = health.get("workers") or {}
    queues = health.get("queues") or {}
    publications = health.get("publications") or {}
    spend = health.get("spend") or {}
    providers = health.get("providers") or {}
    qc = health.get("media_qc") or {}
    failures = health.get("recent_failures") or []

    youtube = providers.get("youtube") or {}
    instagram = providers.get("instagram") or {}

    lines = [
        f"🩺 <b>Pipeline Health</b>",
        f"{_divider()}",
        "",
        f"  {icons.get(status, '⚪')}  State: <b>{status.upper()}</b>",
        f"  🖥  Workers: {workers.get('online', 0)}/{workers.get('total', 0)} online",
        f"  📥  Queues: {queues.get('queued', 0)} queued · "
        f"{queues.get('awaiting_approval', 0)} to approve · {queues.get('failed', 0)} failed",
        "",
        "  <b>Delivery</b>",
        f"  📤  Published today: {publications.get('published_today', 0)}"
        f"/{publications.get('daily_limit', 0)} · "
        f"{publications.get('remaining_today', 0)} left",
        f"  🎬  Media QC: {qc.get('passed', 0)}/{qc.get('measured', 0)} measured renders passed",
        "",
        "  <b>Providers</b>",
        f"  ▶️  YouTube: {'ready' if youtube.get('credential') else 'no credential'}"
        f" · {'enabled' if youtube.get('enabled') else 'disabled'}",
        f"  📸  Instagram: {'ready' if instagram.get('credential') else 'no credential'}"
        f" · {'enabled' if instagram.get('enabled') else 'disabled'}",
        f"  💰  Spend today: ${float(spend.get('today_usd', 0.0)):.2f}",
    ]
    issues = health.get("issues") or []
    if issues:
        lines += ["", "  <b>Attention</b>"]
        lines += [f"  ⚠️  {str(item).replace('_', ' ')}" for item in issues]
    if failures:
        lines += ["", "  <b>Recent failures</b>"]
        for failure in failures[:3]:
            error = str(failure.get("error") or "")[:70]
            lines.append(f"  🔴  {failure.get('topic')} — {error}")
    lines.append("")
    lines.append(_divider())
    return "\n".join(lines)


def _build_series_panel() -> str:
    from .series import list_series, series_state

    try:
        series_list = list_series(limit=10)
    except Exception:
        series_list = []
    if not series_list:
        return (
            "📚 <b>Story Series</b>\n"
            f"{_divider()}\n\n"
            "  No series yet.\n\n"
            "  A series tells one story in six parts: the engine plans the whole "
            "arc, every part ends on a cliffhanger, and the parts are released "
            "one at a time so the audience stays warm.\n\n"
            "  Tap <b>New story series</b> to start one."
        )
    lines = ["📚 <b>Story Series</b>", _divider(), ""]
    for series in series_list:
        state = series_state(series.id)
        published = sum(1 for part in state["parts"] if part["published"])
        lines.append(
            f"  📖 <code>{series.id[:8]}</code> {html.escape(str(series.topic))} — "
            f"{published}/{state['part_count']} published · {state['status']}"
        )
    lines += ["", "Tap a series to open it."]
    return "\n".join(lines)


def _series_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .series import list_series

    rows: list[list[dict[str, str]]] = []
    for series in list_series(limit=10):
        rows.append([_button(f"📖 {series.topic[:36]}", f"series:{series.id}")])
    rows.append([_button("➕ New story series", "series_new")])
    rows.append([_button("🔙 Back", "nav:studio")])
    return {"inline_keyboard": rows}


def _build_series_card(series_id: str) -> str:
    from .series import series_state

    try:
        state = series_state(series_id)
    except ValueError:
        return "⚠️ Series not found."
    lines = [
        f"📖 <b>{html.escape(str(state['topic']))}</b>",
        _divider(),
        "",
        f"  Format: <b>{html.escape(str(state['pack_label']))}</b>",
        f"  Parts: <b>{state['part_count']}</b> · Cadence: <b>{html.escape(str(state['cadence_label']))}</b>",
        f"  Budget: <b>${float(state['budget_usd']):.2f}</b> · Status: <b>{state['status']}</b>",
        "",
    ]
    for part in state["parts"]:
        emoji = "✅" if part["published"] else _STATUS_EMOJI.get(part["status"], "⚪")
        title = html.escape(str(part["title"])[:60])
        when = ""
        if part.get("scheduled_at"):
            when = f" · {html.escape(str(part['scheduled_at'])[:16])}"
        lines.append(f"  {emoji} भाग {part['part_index']} — {title} <i>{part['status']}</i>{when}")
    lines.append("")
    lines.append("Parts release one at a time; each one needs the previous part to be live first.")
    return "\n".join(lines)


def _series_card_keyboard(series_id: str) -> dict[str, list[list[dict[str, str]]]]:
    from .series import series_jobs

    rows: list[list[dict[str, str]]] = []
    try:
        jobs = series_jobs(series_id)
    except Exception:
        jobs = []
    for job in jobs:
        if job.status in {"awaiting_script_approval", "approved", "awaiting_approval"}:
            label = {
                "awaiting_script_approval": "📝 Review script",
                "approved": "📤 Publish now",
                "awaiting_approval": "✅ Approve video",
            }.get(job.status, "Open")
            action = {
                "awaiting_script_approval": "script_approve",
                "approved": "publish",
                "awaiting_approval": "approve",
            }.get(job.status, "queue")
            rows.append([_button(f"{label} · भाग {job.topic[-3:]}", f"{action}:{job.id}")])
    rows.append([_button("⏭ Release next part now", f"series_release:{series_id}")])
    rows.append([_button("⏸ Pause series", f"series_status:{series_id}:paused")])
    rows.append([_button("🔙 Back", "series_panel")])
    return {"inline_keyboard": rows}


def _build_credits_message() -> str:
    from .credits import active_series_spend, money_summary

    try:
        summary = money_summary()
    except Exception:
        summary = {}
    if not summary or not summary.get("balance_usd"):
        return (
            "💳 <b>Higgsfield API balance</b>\n"
            f"{_divider()}\n\n"
            "  The API balance is not set yet.\n\n"
            "  The Higgsfield API is billed separately from the website plan: it runs "
            "on its own dollar balance, charged per generation.\n\n"
            "  Tap <b>Set API balance</b> and send the dollar amount on the account."
        )
    lines = [
        "💳 <b>Higgsfield API balance</b>",
        _divider(),
        "",
        f"  Balance: <b>${float(summary['balance_usd']):.2f}</b>",
        f"  Spent: <b>${float(summary['spent_usd']):.2f}</b>",
        f"  Remaining: <b>${float(summary['remaining_usd']):.2f}</b>",
        f"  Cap per series: <b>${float(summary['series_cap_usd']):.2f}</b>",
    ]
    spend = active_series_spend()
    if spend:
        lines += ["", "  <b>Per series</b>"]
        for item in spend:
            lines.append(
                f"  📖 {html.escape(str(item['topic'])[:34])} — "
                f"${float(item['spent_usd']):.2f}/${float(item['cap_usd']):.2f} ({item['status']})"
            )
    return "\n".join(lines)


def _credits_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {"inline_keyboard": [
        [_button("💳 Set API balance", "credits_set_balance")],
        [_button("🎯 Set per-series cap", "credits_set_cap")],
        [_button("🔙 Back", "nav:ops")],
    ]}


def _build_pack_panel() -> str:
    from .hq import get_active_pack_id
    from .packs import PACKS

    active = get_active_pack_id()
    lines = ["🎭 <b>Format packs</b>", _divider(), ""]
    for pack in PACKS.values():
        mark = "✅" if pack.pack_id == active else "▫️"
        series_note = " · series-ready" if pack.series_ready else ""
        lines.append(f"  {mark} <b>{html.escape(pack.label)}</b>{series_note}")
        lines.append(f"     <i>{html.escape(pack.summary)}</i>")
    lines += ["", "The active pack decides the writer style, the hook, the visuals and the hashtags."]
    return "\n".join(lines)


def _pack_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .hq import get_active_pack_id
    from .packs import PACKS

    active = get_active_pack_id()
    rows: list[list[dict[str, str]]] = []
    for pack in PACKS.values():
        mark = "✅ " if pack.pack_id == active else ""
        rows.append([_button(f"{mark}{pack.label[:40]}", f"pack_set:{pack.pack_id}")])
    rows.append([_button("🔙 Back", "nav:studio")])
    return {"inline_keyboard": rows}


def _series_pack_keyboard() -> str:
    from .packs import SERIES_PACKS, PACKS

    rows = [
        [_button(PACKS[pack_id].label[:40], f"series_pack:{pack_id}")]
        for pack_id in SERIES_PACKS
    ]
    rows.append([_button("🔙 Back", "series_panel")])
    return {"inline_keyboard": rows}


def _series_cadence_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    from .series import CADENCES

    rows = [
        [_button(str(info["label"]), f"series_cadence:{key}")]
        for key, info in CADENCES.items()
    ]
    rows.append([_button("🔙 Back", "series_panel")])
    return {"inline_keyboard": rows}


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
            "<code>instagram_content_publish</code>, "
            "<code>instagram_manage_media</code> (needed to remove or delete "
            "published posts), and "
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
        [_button("➕ Add YouTube account slot", "account_add:youtube"), _button("➕ Add Instagram account slot", "account_add:instagram")],
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
    from .platform_accounts import list_accounts

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
    youtube_slots = list_accounts("youtube")
    instagram_slots = list_accounts("instagram")
    youtube_slot_text = (
        "\n".join(
            f"   {'🟢' if account['enabled'] else '⚪'} {html.escape(str(account['name']))}"
            for account in youtube_slots
        )
        or "   No named slots yet"
    )
    instagram_slot_text = (
        "\n".join(
            f"   {'🟢' if account['enabled'] else '⚪'} {html.escape(str(account['name']))}"
            for account in instagram_slots
        )
        or "   No named slots yet"
    )
    return (
        f"🔗 <b>Channel accounts</b>\n{_divider()}\n\n"
        "<i>One connected account per platform is supported. Named publishing slots: up to 3 YouTube and 2 Instagram.</i>\n\n"
        f"▶️ <b>YouTube:</b> {youtube_state}\n"
        f"{youtube_slot_text}\n"
        f"   Publishing: {'enabled' if youtube_enabled else 'disabled'}\n"
        f"{youtube_setup_note}"
        f"📸 <b>Instagram:</b> "
        f"{'ready' if instagram_token_configured and instagram_id and instagram_public_url else 'setup required'}\n"
        f"{instagram_slot_text}\n"
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
        f"A script draft is created for "
        f"<b>{html.escape(PUBLISH_TARGETS[get_publish_target()])}</b> in the current format "
        f"({html.escape(str(get_format()['label']))}, niche: "
        f"{html.escape(get_niche_label())}) and sent to Script Review before any media provider runs. "
        f"Nothing is generated or published without your approval. Change format or "
        f"niche any time in ⚙️ Controls.\n"
        f"\n"
        f"Or let Automation do it: open 🤖 Automation and run a cycle — "
        f"it builds a job from the top trend.\n"
        f"\n"
        f"{_divider()}"
    )


def _build_custom_creator_message() -> str:
    return (
        "🧠 <b>Custom Creator</b>\n"
        f"{_divider()}\n\n"
        "First choose the number of parts, then send one natural-language brief.\n"
        "The built-in planning AI will propose:\n"
        "  • connected story arc and exact part count\n"
        "  • tone and target audience\n"
        "  • script length and cliffhanger style\n"
        "  • scene, camera and caption direction\n"
        "  • title, thumbnail and hashtag preferences\n\n"
        "<b>Example:</b>\n"
        "<i>Create 7 cyber-safety Shorts. Serious tone, 45 seconds each, "
        "cinematic scenes, simple Hindi, and a cliffhanger at every part.</i>\n\n"
        "Nothing is published automatically. The bot creates separate preview "
        "jobs and waits for per-job approval."
        f"\n{_divider()}"
    )


def _custom_part_count_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    counts = [2, 3, 4, 5, 6, 7, 8, 10, 12]
    rows = [
        [_button(f"{count} Parts", f"custom_parts:{count}") for count in counts[:3]],
        [_button(f"{count} Parts", f"custom_parts:{count}") for count in counts[3:6]],
        [_button(f"{count} Parts", f"custom_parts:{count}") for count in counts[6:]],
        [_button("❌ Cancel", "custom_cancel"), _button("🔙 Back", "menu")],
    ]
    return {"inline_keyboard": rows}


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

        if message and chat_id in _pending_icon_slot:
            await _handle_icon_input(chat_id, message)
            return
        if message and chat_id in _pending_clip_source:
            await _handle_clip_source(chat_id, message, update)
            return
        if message and chat_id in _pending_custom_script:
            await _handle_custom_script(chat_id, message)
            return
        if message and chat_id in _pending_custom_voice:
            await _handle_custom_voice(chat_id, message)
            return
        if message and chat_id in _pending_custom_cover:
            await _handle_custom_cover(chat_id, message)
            return
        if message and chat_id in _pending_auto_cost:
            text = str(message.get("text") or "").strip()
            if text == "/cancel":
                _pending_auto_cost.discard(chat_id)
                await send_message(chat_id, "↩️ Cost ceiling unchanged.", _auto_keyboard())
                return
            from .hq import set_auto_publish_max_cost_usd

            try:
                saved = set_auto_publish_max_cost_usd(float(text.replace("$", "").strip()))
            except (TypeError, ValueError):
                await send_message(
                    chat_id,
                    "⚠️ Enter a number between 0.10 and 25, for example 1.50.",
                    _back_keyboard(),
                )
                return
            _pending_auto_cost.discard(chat_id)
            await send_message(
                chat_id,
                f"💵 <b>Cost ceiling set to ${saved:.2f}</b> per video.",
                _auto_keyboard(),
            )
            return
        if message and chat_id in _pending_watermark_text:
            text = str(message.get("text") or "").strip()
            if text == "/cancel":
                _pending_watermark_text.discard(chat_id)
                await send_message(chat_id, "↩️ Watermark text unchanged.", _settings_keyboard())
                return
            from .hq import set_watermark_text

            try:
                saved = set_watermark_text(text)
            except ValueError as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}\nSend the watermark text or /cancel.",
                    _back_keyboard(),
                )
                return
            _pending_watermark_text.discard(chat_id)
            await send_message(
                chat_id,
                f"🔒 <b>Watermark text set</b>\n{html.escape(saved)}",
                _settings_keyboard(),
            )
            return
        if message and chat_id in _pending_audio_upload:
            await _handle_audio_upload(chat_id, message)
            return

        # ── Callback query handling ─────────────────────────────────
        if callback:
            await answer_callback(str(callback.get("id", "")), "⏳ Working…")
            data = callback.get("data", "")

            if data == "nav:studio":
                await send_message(chat_id, _navigation_message("studio"), _studio_keyboard())

            elif data == "nav:review":
                await send_message(chat_id, _navigation_message("review"), _review_keyboard())

            elif data == "nav:insights":
                await send_message(chat_id, _navigation_message("insights"), _insights_keyboard())

            elif data == "nav:ops":
                await send_message(chat_id, _navigation_message("ops"), _operations_keyboard())

            elif data == "nav:settings":
                await send_message(chat_id, _navigation_message("settings"), _settings_keyboard())

            elif data.startswith("icon:panel:"):
                try:
                    page = int(data.rsplit(":", 1)[1])
                except ValueError:
                    page = 0
                await send_message(chat_id, _icon_panel_message(page), _icon_panel_keyboard(page))

            elif data.startswith("icon:set:"):
                slot = data.split(":", 2)[2]
                if slot not in ICON_SLOTS:
                    await send_message(chat_id, "⚠️ Unknown premium icon slot.", _settings_keyboard())
                    return
                _pending_icon_slot[chat_id] = slot
                await send_message(
                    chat_id,
                    f"💎 <b>Set {html.escape(slot.replace('_', ' ').title())}</b>\n\n"
                    "Send one Telegram custom emoji or its custom_emoji_id. "
                    "Use /clear to remove this slot or /cancel to stop.",
                    _back_keyboard(),
                )

            elif data in {"icon:enable", "icon:disable"}:
                set_icons_enabled(data == "icon:enable")
                await send_message(chat_id, _icon_panel_message(0), _icon_panel_keyboard(0))

            elif data == "icon:test":
                await send_message(
                    chat_id,
                    "💎 <b>Premium icon preview</b>\n\n"
                    "If custom button icons are unsupported for this bot/account, this message automatically retries with Unicode.",
                    _keyboard(),
                )

            elif data == "clip_studio":
                await send_message(chat_id, _clip_studio_message(), _clip_studio_keyboard())

            elif data.startswith("clip_new:"):
                parts = data.split(":")
                source_kind = parts[1] if len(parts) > 1 else ""
                if source_kind not in {"vod", "live"}:
                    await send_message(chat_id, "⚠️ Invalid clipping mode.", _clip_studio_keyboard())
                    return
                capture_seconds = None
                if source_kind == "live":
                    try:
                        capture_seconds = int(parts[2])
                    except (IndexError, ValueError):
                        capture_seconds = 900
                _pending_clip_source[chat_id] = (source_kind, capture_seconds)
                await send_message(
                    chat_id,
                    f"🎞 <b>Submit {html.escape(source_kind.upper())} source</b>\n\n"
                    "Send the public HTTPS video/live URL as your next message. "
                    "No browser cookies or private account sessions are used. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data.startswith("clip_job:"):
                job_id = data.split(":", 1)[1]
                await send_message(chat_id, _clip_job_message(job_id), _clip_job_keyboard(job_id))

            elif data.startswith("clip_retry:"):
                job_id = data.split(":", 1)[1]
                job = retry_clip_job(job_id)
                await send_message(
                    chat_id,
                    f"🔁 <b>Clip job {job.id[:8]} queued for retry.</b>",
                    _clip_job_keyboard(job.id),
                )

            elif data.startswith("clip_preview:"):
                candidate_id = data.split(":", 1)[1]
                await _send_clip_candidate_preview(chat_id, candidate_id)

            elif data.startswith("clip_review:"):
                parts = data.split(":")
                if len(parts) != 3 or parts[1] not in {"approved", "rejected"}:
                    await send_message(chat_id, "⚠️ Invalid clip review.", _clip_studio_keyboard())
                    return
                candidate = review_clip_candidate(parts[2], parts[1], user_id)
                await send_message(
                    chat_id,
                    f"{'✅' if candidate.status == 'approved' else '❌'} "
                    f"<b>Clip #{candidate.rank} {html.escape(candidate.status)}</b>",
                    _clip_job_keyboard(candidate.job_id),
                )

            elif data.startswith("clip_promote:"):
                candidate_id = data.split(":", 1)[1]
                job = promote_clip_candidate(candidate_id, get_publish_target(), user_id)
                await send_message(
                    chat_id,
                    "📤 <b>Clip moved to the publish queue</b>\n\n"
                    f"{_format_job_card(job)}\n\n"
                    "It is approved for the selected destination; publishing still requires your explicit action.",
                    _job_keyboard(job),
                )

            elif data == "pause":
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

            elif data == "custom_creator":
                await send_message(
                    chat_id,
                    _build_custom_creator_message(),
                    _custom_part_count_keyboard(),
                )

            elif data.startswith("custom_parts:"):
                try:
                    count = int(data.split(":", 1)[1])
                except (TypeError, ValueError):
                    count = 6
                count = max(2, min(count, 12))
                _pending_custom_creator.add(chat_id)
                _pending_custom_part_count[chat_id] = count
                await send_message(
                    chat_id,
                    "🧠 <b>Custom Series setup</b>\n"
                    f"{_divider()}\n\n"
                    f"  Parts selected: <b>{count}</b>\n"
                    "  Planning mode: <b>Premium built-in AI assistant</b>\n"
                    "  Output: scripts, scenes, titles, thumbnails, captions and hashtags\n\n"
                    "Now send the story idea, niche, tone, audience and any special hook you want.",
                    {"inline_keyboard": [[_button("❌ Cancel", "custom_cancel")], [_button("🔙 Change parts", "custom_creator")]]},
                )

            elif data == "custom_cancel":
                _pending_custom_creator.discard(chat_id)
                _pending_custom_part_count.pop(chat_id, None)
                await send_message(chat_id, "↩️ Custom Creator cancelled.", _keyboard())

            elif data == "series_panel":
                await send_message(chat_id, _build_series_panel(), _series_keyboard())

            elif data.startswith("series:"):
                series_id = data.split(":", 1)[1]
                await send_message(
                    chat_id,
                    _build_series_card(series_id),
                    _series_card_keyboard(series_id),
                )

            elif data == "series_new":
                _pending_series_topic[chat_id] = ""
                await send_message(
                    chat_id,
                    "📚 <b>New story series</b>\n"
                    f"{_divider()}\n\n"
                    "  Send the story idea in one line: the topic, the world or the "
                    "conflict you want the six parts to tell.\n\n"
                    "  Example: <i>शेरू भाई का सबसे बड़ा दुश्मन</i>",
                    {"inline_keyboard": [[_button("❌ Cancel", "series_panel")]]},
                )

            elif data.startswith("series_pack:"):
                pack_id = data.split(":", 1)[1]
                _pending_series_pack[chat_id] = pack_id
                await send_message(
                    chat_id,
                    "🎭 <b>Choose the format pack</b>\n"
                    f"{_divider()}\n\n"
                    "  The pack decides the character, the story beats, the hook and the look.",
                    _series_pack_keyboard(),
                )

            elif data.startswith("series_cadence:"):
                cadence = data.split(":", 1)[1]
                topic = _pending_series_topic.get(chat_id, "").strip()
                pack_id = _pending_series_pack.get(chat_id, "")
                _pending_series_topic.pop(chat_id, None)
                _pending_series_pack.pop(chat_id, None)
                if not topic or not pack_id:
                    await send_message(chat_id, "⚠️ Start the series again from the series panel.", _keyboard())
                    return
                from .series import create_series, series_state

                progress_message_id = await send_progress_message(
                    chat_id,
                    "📚 <b>Planning the story…</b>\n"
                    "<code>[████░░░░]  Series bible</code>\n"
                    "<code>[░░░░░░░░]  Six parts with cliffhangers</code>\n"
                    "<code>[░░░░░░░░]  Character and scene direction</code>",
                )
                try:
                    series, jobs = create_series(
                        topic,
                        pack_id,
                        part_count=6,
                        cadence=cadence,
                        budget_usd=5.0,
                        platform=get_publish_target(),
                    )
                    state = series_state(series.id)
                    await finish_progress_message(
                        chat_id,
                        progress_message_id,
                        (
                            f"✅ <b>Series planned: {html.escape(str(state['topic']))}</b>\n"
                            f"{_divider()}\n\n"
                            f"  Parts: {state['part_count']} · Cadence: {html.escape(str(state['cadence_label']))}\n"
                            f"  Budget cap: ${float(state['budget_usd']):.2f}\n\n"
                            "  भाग 1 is queued for its script. The other parts stay held until "
                            "the part before them is live.\n\n"
                            "  🛡️ Publishing still needs your approval."
                        ),
                        _series_card_keyboard(series.id),
                    )
                    progress_message_id = None
                except Exception as exc:
                    logger.exception("Series creation failed")
                    await finish_progress_message(
                        chat_id,
                        progress_message_id,
                        f"⚠️ Could not create the series: {_safe_error_text(exc)}",
                        _keyboard(),
                    )
                    progress_message_id = None

            elif data.startswith("series_release:"):
                from .series import release_next_part, series_state

                series_id = data.split(":", 1)[1]
                try:
                    released = release_next_part(series_id)
                except Exception as exc:
                    await send_message(chat_id, f"⚠️ {_safe_error_text(exc)}", _keyboard())
                    return
                await send_message(
                    chat_id,
                    (
                        "⏭ <b>Next part released</b>\n\n"
                        + (
                            f"  भाग {released.id[-3:]} is now queued for production."
                            if released is not None
                            else "  Every part of this series is already live."
                        )
                    ),
                    _series_card_keyboard(series_id),
                )

            elif data.startswith("series_status:"):
                from .series import set_series_status, series_state

                parts = data.split(":")
                if len(parts) != 3:
                    await send_message(chat_id, "⚠️ Invalid series action.", _keyboard())
                    return
                series_id, status = parts[1], parts[2]
                try:
                    set_series_status(series_id, status)
                except Exception as exc:
                    await send_message(chat_id, f"⚠️ {_safe_error_text(exc)}", _keyboard())
                    return
                await send_message(
                    chat_id,
                    _build_series_card(series_id),
                    _series_card_keyboard(series_id),
                )

            elif data == "pack_panel":
                await send_message(chat_id, _build_pack_panel(), _pack_keyboard())

            elif data == "credits_panel":
                await send_message(chat_id, _build_credits_message(), _credits_keyboard())

            elif data in {"credits_set_balance", "credits_set_cap"}:
                _pending_credit_input[chat_id] = (
                    "balance" if data == "credits_set_balance" else "cap"
                )
                await send_message(
                    chat_id,
                    (
                        "💳 <b>Set the API balance</b>\n\n"
                        "  Send the dollar amount on the Higgsfield API account"
                        if data == "credits_set_balance"
                        else "💳 <b>Set the per-series cap</b>\n\n"
                        "  Send how many dollars one story series may use"
                    ),
                    _back_keyboard(),
                )

            elif data.startswith("pack_set:"):
                from .hq import set_active_pack

                pack_id = data.split(":", 1)[1]
                try:
                    label = set_active_pack(pack_id)
                except ValueError:
                    await send_message(chat_id, "⚠️ Unknown format pack.", _keyboard())
                    return
                await send_message(
                    chat_id,
                    f"🎭 <b>Format pack set</b>\n\n  {html.escape(label)}\n\n"
                    "  New jobs now use this format.",
                    _pack_keyboard(),
                )

            elif data == "controls":
                await send_message(chat_id, _build_controls_message(), _controls_keyboard())

            elif data == "audio_catalog":
                await send_message(chat_id, _audio_catalog_message(), _audio_catalog_keyboard())

            elif data.startswith("audio_add:"):
                kind = data.split(":", 1)[1]
                if kind not in {"bgm", "sfx"}:
                    await send_message(chat_id, "⚠️ Unsupported audio type.", _audio_catalog_keyboard())
                    return
                _pending_audio_upload[chat_id] = kind
                await send_message(
                    chat_id,
                    f"🎵 <b>Upload {html.escape(kind.upper())}</b>\n\n"
                    "Send an MP3, WAV, OGG, or M4A file up to 20 MB. "
                    "Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data.startswith("audio_default:"):
                parts = data.split(":")
                if len(parts) != 3 or parts[1] not in {"bgm", "sfx"}:
                    await send_message(chat_id, "⚠️ Invalid audio selection.", _audio_catalog_keyboard())
                    return
                from .audio_catalog import set_default_audio_asset

                set_default_audio_asset(Path(get_settings().media_root), parts[1], parts[2])
                await send_message(chat_id, "✅ Default audio updated.", _audio_catalog_keyboard())

            elif data.startswith("audio_delete:"):
                asset_id = data.split(":", 1)[1]
                from .audio_catalog import delete_audio_asset

                removed = delete_audio_asset(Path(get_settings().media_root), asset_id)
                await send_message(
                    chat_id,
                    "🗑 Audio removed." if removed else "⚪ Audio asset was not found.",
                    _audio_catalog_keyboard(),
                )

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

            elif data.startswith("account_add:"):
                platform = data.split(":", 1)[1]
                if platform not in {"youtube", "instagram"}:
                    await send_message(chat_id, "⚠️ Unsupported account platform.", _account_manager_keyboard())
                else:
                    _pending_account_name[chat_id] = platform
                    await send_message(
                        chat_id,
                        f"➕ <b>Add {platform.title()} account slot</b>\n"
                        "Send a short label, for example <code>Main Channel</code> or <code>Brand Reel</code>.\n"
                        "Maximum: 3 YouTube and 2 Instagram account slots.",
                        {"inline_keyboard": [[_button("❌ Cancel", "accounts")]]},
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

            elif data == "voice_catalog":
                await send_message(
                    chat_id,
                    "🎭 <b>Hindi Voice Browser</b>\n\n"
                    "Fish lists provider-official Hindi designs, MiniMax lists documented system voices, "
                    "and ElevenLabs lists Hindi voices available to your account.",
                    _voice_catalog_home_keyboard(),
                )

            elif data.startswith("voice_catalog:") or data.startswith("voice_catalog_refresh:"):
                parts = data.split(":")
                provider = parts[1] if len(parts) > 1 else ""
                try:
                    page = int(parts[2]) if len(parts) > 2 else 0
                except ValueError:
                    page = 0
                if provider not in {"fish_audio", "minimax", "elevenlabs"}:
                    await send_message(chat_id, "⚠️ Unsupported voice catalog.", _voice_catalog_home_keyboard())
                    return
                force_refresh = data.startswith("voice_catalog_refresh:")
                if force_refresh or provider not in _voice_catalog_results:
                    from .voice_catalog import discover_hindi_voices

                    try:
                        options = await asyncio.to_thread(discover_hindi_voices, provider)
                    except Exception as exc:
                        logger.warning("Voice catalog lookup failed (%s)", type(exc).__name__)
                        await send_message(
                            chat_id,
                            f"⚠️ {html.escape(provider.title())} catalog is unavailable. "
                            "Check its API key and provider status.",
                            _voice_catalog_home_keyboard(),
                        )
                        return
                    _cache_voice_options(provider, options)
                await send_message(
                    chat_id,
                    _voice_catalog_message(provider, page),
                    _voice_catalog_keyboard(provider, page),
                )

            elif data.startswith("voice_choose:"):
                parts = data.split(":")
                if len(parts) != 3:
                    await send_message(chat_id, "⚠️ Invalid voice selection.", _voice_catalog_home_keyboard())
                    return
                provider, token = parts[1], parts[2]
                try:
                    option = _voice_from_token(provider, token)
                except ValueError as exc:
                    await send_message(chat_id, f"⚠️ {html.escape(str(exc))}", _voice_catalog_home_keyboard())
                    return
                await send_message(
                    chat_id,
                    "🎭 <b>Confirm voice selection</b>\n\n"
                    f"Provider: <b>{html.escape(provider.replace('_', ' ').title())}</b>\n"
                    f"Voice: <b>{html.escape(str(option.get('name') or 'Voice'))}</b>\n"
                    f"Source: {html.escape(str(option.get('source') or ''))}\n\n"
                    "This selection applies to new jobs. Run the blind benchmark before production.",
                    {"inline_keyboard": [
                        [_button("Confirm selection", f"voice_confirm:{provider}:{token}")],
                        [_button("Cancel", f"voice_catalog:{provider}:0")],
                    ]},
                )

            elif data.startswith("voice_confirm:"):
                parts = data.split(":")
                if len(parts) != 3:
                    await send_message(chat_id, "⚠️ Invalid voice confirmation.", _voice_catalog_home_keyboard())
                    return
                provider, token = parts[1], parts[2]
                option = _voice_from_token(provider, token)
                voice_id = str(option.get("voice_id") or "")
                if provider == "fish_audio":
                    set_fish_audio_reference_id(voice_id)
                elif provider == "minimax":
                    set_minimax_voice_id(voice_id)
                elif provider == "elevenlabs":
                    set_elevenlabs_voice_id(voice_id)
                else:
                    raise ValueError("Unsupported voice provider")
                ready = bool(provider_status().get(provider))
                if ready:
                    set_provider_for_role("voice", provider)
                await send_message(
                    chat_id,
                    f"✅ <b>{html.escape(str(option.get('name') or 'Voice'))} selected.</b>\n"
                    + (
                        "The provider is now active for new voice generations."
                        if ready
                        else "Add this provider's API key before activating it for generation."
                    ),
                    _provider_role_keyboard("voice"),
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

            elif data == "elevenlabs_tts_model":
                await send_message(
                    chat_id,
                    "🎙 <b>ElevenLabs TTS model</b>\n\n"
                    "Turbo is the cost-efficient production default. Benchmark expressive models before switching.",
                    _elevenlabs_tts_model_keyboard(),
                )

            elif data.startswith("elevenlabs_tts_model_set:"):
                model_id = data.split(":", 1)[1]
                try:
                    set_elevenlabs_tts_model(model_id)
                except ValueError:
                    await send_message(
                        chat_id,
                        "⚠️ Unsupported ElevenLabs model.",
                        _elevenlabs_tts_model_keyboard(),
                    )
                    return
                await send_message(
                    chat_id,
                    f"✅ <b>ElevenLabs model set to {html.escape(model_id)}</b>",
                    _elevenlabs_tts_model_keyboard(),
                )


            elif data == "fish_audio_reference_id":
                _pending_fish_audio_reference_id.add(chat_id)
                await send_message(
                    chat_id,
                    "🐟 <b>Set Fish Audio reference voice</b>\n\n"
                    "Send the reference/model ID from your Fish Audio voice page. "
                    "This is an identifier, not the API key. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data == "fish_audio_model":
                await send_message(
                    chat_id,
                    "🐟 <b>Fish Audio model</b>\n\n"
                    "S2.1 Pro is the production default. The free model is for testing; "
                    "confirm your plan before using generated audio commercially.",
                    _fish_audio_model_keyboard(),
                )

            elif data.startswith("fish_audio_model_set:"):
                model_id = data.split(":", 1)[1]
                try:
                    set_fish_audio_model_id(model_id)
                except ValueError:
                    await send_message(
                        chat_id,
                        "⚠️ Unsupported Fish Audio model.",
                        _fish_audio_model_keyboard(),
                    )
                    return
                await send_message(
                    chat_id,
                    f"✅ <b>Fish Audio model set to {html.escape(model_id)}</b>",
                    _fish_audio_model_keyboard(),
                )

            elif data == "minimax_voice_id":
                _pending_minimax_voice_id.add(chat_id)
                await send_message(
                    chat_id,
                    "🎚 <b>Set MiniMax speech voice ID</b>\n\n"
                    "Send a MiniMax system, designed, or cloned voice ID. "
                    "This is not the API key. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data == "minimax_speech_model":
                await send_message(
                    chat_id,
                    "🎚 <b>MiniMax speech model</b>\n\n"
                    "Turbo is the cost-efficient default; HD is available for benchmark comparison.",
                    _minimax_speech_model_keyboard(),
                )

            elif data.startswith("minimax_speech_model_set:"):
                model_id = data.split(":", 1)[1]
                try:
                    set_minimax_speech_model(model_id)
                except ValueError:
                    await send_message(
                        chat_id,
                        "⚠️ Unsupported MiniMax speech model.",
                        _minimax_speech_model_keyboard(),
                    )
                    return
                await send_message(
                    chat_id,
                    f"✅ <b>MiniMax speech model set to {html.escape(model_id)}</b>",
                    _minimax_speech_model_keyboard(),
                )

            elif data == "voice_benchmark":
                await send_message(
                    chat_id,
                    "🎧 <b>Run a paid voice benchmark?</b>\n\n"
                    "The factory will synthesize the same Hindi/Hinglish script with every ready "
                    "Fish Audio, ElevenLabs, and MiniMax voice. This consumes provider credits. "
                    "At least two providers must be fully configured and no-paid mode must be OFF.",
                    {"inline_keyboard": [
                        [_button("Run benchmark", "voice_benchmark_run")],
                        [_button("Cancel", "provider_role:voice")],
                    ]},
                )

            elif data == "voice_benchmark_run":
                task = asyncio.create_task(_run_voice_benchmark_and_send(chat_id))
                _track_background_task(task)
                await send_message(
                    chat_id,
                    "⏳ <b>Voice benchmark started.</b> Samples will arrive here when ready.",
                    _provider_role_keyboard("voice"),
                )

            elif data.startswith("voice_benchmark_reveal:"):
                benchmark_id = data.split(":", 1)[1]
                from .voice_benchmark import reveal_voice_benchmark

                try:
                    answer_key = reveal_voice_benchmark(
                        Path(get_settings().media_root),
                        benchmark_id,
                    )
                except ValueError:
                    await send_message(chat_id, "⚠️ Voice benchmark result was not found.", _provider_role_keyboard("voice"))
                    return
                provider_labels = {
                    "fish_audio": "Fish Audio",
                    "elevenlabs": "ElevenLabs",
                    "minimax": "MiniMax Speech",
                }
                lines = [
                    f"{html.escape(label)}: <b>{html.escape(provider_labels.get(provider, provider))}</b>"
                    for label, provider in sorted(answer_key.items())
                ]
                await send_message(
                    chat_id,
                    "🎧 <b>Voice benchmark reveal</b>\n\n" + "\n".join(lines),
                    _provider_role_keyboard("voice"),
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
                if provider == "minimax" and role == "script" and not status.get("minimax_mplan"):
                    await send_message(
                        chat_id,
                        "⚠️ Add the MiniMax M Plan Subscription API key under API credentials first. "
                        "The Platform API key is reserved for H3 video generation.",
                        _provider_role_keyboard(role),
                    )
                    return
                if provider == "minimax" and role == "voice" and (
                    not status.get("minimax") or not get_minimax_voice_id()
                ):
                    await send_message(
                        chat_id,
                        "⚠️ MiniMax speech is not ready. Add the Platform API key and voice ID first.",
                        _provider_role_keyboard(role),
                    )
                    return
                if provider == "minimax" and role == "video" and not status.get("minimax"):
                    await send_message(
                        chat_id,
                        "⚠️ MiniMax video is not ready. Add the Platform API key first.",
                        _provider_role_keyboard(role),
                    )
                    return
                if provider == "higgsfield" and role == "video" and not status.get("higgsfield"):
                    await send_message(
                        chat_id,
                        "⚠️ Higgsfield video is not ready. Add its Key ID:Key Secret first.",
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
                if provider == "fish_audio" and (
                    not status.get("fish_audio") or not get_fish_audio_reference_id()
                ):
                    await send_message(
                        chat_id,
                        "⚠️ Fish Audio is not ready. Add its API key and reference voice ID first.",
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

                ready_state = "not configured"
                if get_settings().public_base_url:
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            response = await client.get(f"{get_settings().public_base_url.rstrip('/')}/ready")
                        ready_state = "ready" if response.is_success else f"HTTP {response.status_code}"
                    except httpx.HTTPError:
                        ready_state = "unreachable"
                configured = bool(
                        get_platform_secret("instagram")
                        or get_settings().instagram_access_token
                )
                await send_message(
                    chat_id,
                    (
                        f"🖥 <b>System health</b>\n{_divider()}\n\n"
                        f"API + database: <b>{html.escape(ready_state)}</b>\n"
                        f"Instagram token: <b>{'configured' if configured else 'missing'}</b>\n"
                        f"Factory: <b>{'paused' if is_paused() else 'running'}</b>\n"
                        f"Automation: <b>{'on' if automation_enabled() else 'off'}</b>\n\n"
                        f"{_build_health_message()}\n\n"
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

            elif data.startswith("provider_remove:"):
                provider = data.split(":", 1)[1]
                if provider not in PROVIDER_INFO:
                    await send_message(chat_id, "⚠️ Unsupported provider.", _provider_keyboard())
                    return
                removed = delete_provider_secret(provider)
                await send_message(
                    chat_id,
                    (
                        f"🗑 <b>{html.escape(str(PROVIDER_INFO[provider]['label']))} key removed.</b>\n\n"
                        "Existing jobs are unchanged. New jobs will not use this credential until you add it again."
                        if removed
                        else "⚪ No stored key was found for this provider."
                    ),
                    _provider_detail_keyboard(provider),
                )

            elif data == "media:enable":
                set_media_generation_enabled(True)
                await send_message(chat_id, "🚀 <b>AI media generation enabled</b>", _provider_keyboard())

            elif data == "media:disable":
                set_media_generation_enabled(False)
                await send_message(chat_id, "⏸️ <b>AI media generation disabled</b>", _provider_keyboard())

            elif data.startswith("script_approve:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid script approval.", _keyboard())
                    return
                job_id, version = parts[1], int(parts[2])
                job = approve_script(job_id, user_id, version)
                task = asyncio.create_task(
                    _generate_assets_and_notify(chat_id, job.id, job.content_version)
                )
                _track_background_task(task)
                await send_message(
                    chat_id,
                    "✅ <b>Script approved</b>\n"
                    f"Version v{job.content_version} is locked. Media generation has started.",
                    _job_keyboard(job),
                )

            elif data.startswith("hook:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid hook selection.", _keyboard())
                    return
                from .hooks import apply_hook_variant

                try:
                    job = apply_hook_variant(parts[1], int(parts[2]))
                except ValueError as exc:
                    await send_message(chat_id, f"⚠️ {exc}", _keyboard())
                    return
                await send_message(
                    chat_id,
                    _script_review_message(job),
                    _job_keyboard(job),
                )

            elif data.startswith("script_regenerate:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid script revision.", _keyboard())
                    return
                job_id, version = parts[1], int(parts[2])
                revised = revise_job(job_id, version)
                job = await asyncio.to_thread(generate_content, revised.id)
                await send_message(
                    chat_id,
                    _script_review_message(job),
                    _job_keyboard(job),
                )

            elif data.startswith("script_reject:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid script rejection.", _keyboard())
                    return
                job_id, version = parts[1], int(parts[2])
                job = cancel_job(job_id, version)
                await send_message(
                    chat_id,
                    f"🗑 <b>Script rejected and job cancelled.</b>\n\n{_format_job_card(job)}",
                    _job_keyboard(job),
                )

            elif data.startswith("script_custom:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid custom script request.", _keyboard())
                    return
                job_id, version = parts[1], int(parts[2])
                _pending_custom_script[chat_id] = (job_id, version)
                await send_message(
                    chat_id,
                    "📝 <b>Send your own script</b>\n\n"
                    "Paste the full narration as your next message. The storyboard and captions are "
                    "rebuilt from it, the version is bumped, and no provider credit is spent. "
                    "Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data.startswith("voice_custom:"):
                parts = data.split(":")
                if len(parts) != 3 or not parts[2].isdigit():
                    await send_message(chat_id, "⚠️ Invalid custom voice request.", _keyboard())
                    return
                job_id, version = parts[1], int(parts[2])
                _pending_custom_voice[chat_id] = (job_id, version)
                await send_message(
                    chat_id,
                    "🎙 <b>Send your own voice-over</b>\n\n"
                    "Upload an MP3 or M4A recording (max 25 MB). It replaces provider narration "
                    "for this job. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data.startswith("cover_custom:"):
                parts = data.split(":")
                if len(parts) != 4 or not parts[3].isdigit():
                    await send_message(chat_id, "⚠️ Invalid custom cover request.", _keyboard())
                    return
                job_id, platform, version = parts[1], parts[2], int(parts[3])
                if platform not in {"youtube", "instagram"}:
                    await send_message(chat_id, "⚠️ Unsupported cover platform.", _keyboard())
                    return
                _pending_custom_cover[chat_id] = (job_id, version, platform)
                await send_message(
                    chat_id,
                    f"🎨 <b>Send your {html.escape(platform.title())} cover</b>\n\n"
                    "Upload a JPEG image (max 12 MB). It replaces the generated cover for this job. "
                    "Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data == "watermark:toggle":
                from .hq import is_watermark_enabled, set_watermark_enabled

                enabled = is_watermark_enabled()
                set_watermark_enabled(not enabled)
                await send_message(
                    chat_id,
                    (
                        "🔒 <b>Watermark OFF</b>\nNew videos will not carry the channel credit."
                        if enabled
                        else "🔒 <b>Watermark ON</b>\nNew videos carry the channel credit in the top-right corner."
                    ),
                    _settings_keyboard(),
                )

            elif data == "watermark:text":
                _pending_watermark_text.add(chat_id)
                await send_message(
                    chat_id,
                    "🔒 <b>Set the watermark text</b>\n\n"
                    "Send the exact text to burn into the top-right corner (max 40 characters, "
                    "for example your channel handle). Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data == "auto:panel":
                await send_message(chat_id, _auto_message(), _auto_keyboard())

            elif data in {"auto:mode_on", "auto:mode_off", "auto:publish_on", "auto:publish_off", "auto:clip_on", "auto:clip_off"}:
                from .hq import (
                    set_auto_clip_publish_enabled,
                    set_auto_mode_enabled,
                    set_auto_publish_enabled,
                )

                if data == "auto:mode_on":
                    set_auto_mode_enabled(True)
                elif data == "auto:mode_off":
                    set_auto_mode_enabled(False)
                elif data == "auto:publish_on":
                    set_auto_publish_enabled(True)
                elif data == "auto:publish_off":
                    set_auto_publish_enabled(False)
                elif data == "auto:clip_on":
                    set_auto_clip_publish_enabled(True)
                else:
                    set_auto_clip_publish_enabled(False)
                await send_message(chat_id, _auto_message(), _auto_keyboard())

            elif data == "auto:cost":
                _pending_auto_cost.add(chat_id)
                await send_message(
                    chat_id,
                    "💵 <b>Set the per-video cost ceiling</b>\n\n"
                    "Send the maximum provider spend in US dollars for one video, "
                    "between 0.10 and 25. Example: 1.50. Send /cancel to stop.",
                    _back_keyboard(),
                )

            elif data == "genre:panel":
                await send_message(chat_id, _genre_message(), _genre_keyboard())

            elif data.startswith("genre_category:"):
                category = data.split(":", 1)[1]
                await send_message(
                    chat_id,
                    _genre_message(category),
                    _genre_keyboard(category),
                )

            elif data.startswith("genre_set:"):
                genre_id = data.split(":", 1)[1]
                from .hq import set_genre_id, set_genre_mode

                try:
                    set_genre_id(genre_id)
                    set_genre_mode("specific")
                except ValueError:
                    await send_message(chat_id, "⚠️ Unknown genre.", _genre_keyboard())
                    return
                await send_message(
                    chat_id,
                    f"🎭 <b>Genre set</b>\n{html.escape(genre_id)}\n\nNew jobs use this genre.",
                    _genre_keyboard(),
                )

            elif data.startswith("genre_shuffle:"):
                from .genres import random_genre
                from .hq import set_genre_id, set_genre_mode

                category = data.split(":", 1)[1]
                genre = random_genre(None if category == "all" else category)
                set_genre_id(genre["id"])
                set_genre_mode("shuffle")
                await send_message(
                    chat_id,
                    f"🎲 <b>Genre shuffled</b>\n{html.escape(genre['label'])}\n\nNew jobs use this genre.",
                    _genre_keyboard(),
                )

            elif data == "genre_random":
                from .genres import random_genre
                from .hq import set_genre_id, set_genre_mode

                genre = random_genre()
                set_genre_id(genre["id"])
                set_genre_mode("random")
                await send_message(
                    chat_id,
                    f"🎰 <b>Random genre</b>\n{html.escape(genre['label'])}\n\nNew jobs use this genre.",
                    _genre_keyboard(),
                )

            elif data.startswith("approve:"):
                parts = data.split(":")
                job_id = parts[1] if len(parts) > 1 else ""
                version = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else None
                job = approve_job(job_id, user_id, version)
                await send_message(
                    chat_id,
                    (
                        f"✅ <b>Job approved</b>\n{_divider()}\n\n"
                        f"{_format_job_card(job)}\n\n"
                        f"Content version v{job.content_version} is now locked.\n"
                        f"{_divider()}"
                    ),
                    _job_keyboard(job),
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

        if chat_id in _pending_credit_input:
            kind = _pending_credit_input.pop(chat_id, None)
            if kind:
                from .credits import set_series_cap, set_starting_balance

                raw = "".join(char for char in text if char.isdigit() or char == ".")
                try:
                    value = float(raw)
                except ValueError:
                    await send_message(chat_id, "⚠️ Send a dollar amount, for example 25.", _credits_keyboard())
                    return
                if kind == "balance":
                    set_starting_balance(value)
                    await send_message(
                        chat_id,
                        f"💳 API balance set to <b>${value:.2f}</b>.",
                        _credits_keyboard(),
                    )
                else:
                    set_series_cap(value)
                    await send_message(
                        chat_id,
                        f"🎯 Per-series cap set to <b>${value:.2f}</b>.",
                        _credits_keyboard(),
                    )
                return

        if chat_id in _pending_series_topic:
            if text == "/cancel":
                _pending_series_topic.pop(chat_id, None)
                _pending_series_pack.pop(chat_id, None)
                await send_message(chat_id, "↩️ Series creation cancelled.", _series_keyboard())
                return
            topic = " ".join(text.split()).strip()
            if not topic:
                await send_message(chat_id, "⚠️ Send the story idea in one line.", _keyboard())
                return
            if len(topic) > 240:
                await send_message(chat_id, "⚠️ Keep the story idea under 240 characters.", _keyboard())
                return
            _pending_series_topic[chat_id] = topic
            await send_message(
                chat_id,
                f"📚 <b>Story: {html.escape(topic)}</b>\n"
                f"{_divider()}\n\n"
                "  Choose the format pack for this series.",
                _series_pack_keyboard(),
            )
            return

        if chat_id in _pending_custom_creator:
            if text == "/cancel":
                _pending_custom_creator.discard(chat_id)
                _pending_custom_part_count.pop(chat_id, None)
                await send_message(chat_id, "↩️ Custom Creator cancelled.", _keyboard())
                return
            _pending_custom_creator.discard(chat_id)
            count = _pending_custom_part_count.pop(chat_id, None)
            brief = " ".join(text.split()).strip()
            if len(brief) > 240:
                await send_message(chat_id, "⚠️ Keep the custom brief under 240 characters.", _keyboard())
                return
            if count is None:
                count_match = re.search(
                    r"\b([2-9]|1[0-2])\s*(?:-?\s*(?:part|parts|हिस्से|पार्ट|parts))",
                    brief,
                    flags=re.IGNORECASE,
                )
                count = int(count_match.group(1)) if count_match else 6
            count = max(2, min(count, 12))
            progress_message_id = await send_progress_message(
                chat_id,
                f"🧠 <b>Custom Creator is planning {count} parts…</b>\n"
                "<code>[████░░░░]  Story brief</code>\n"
                "<code>[░░░░░░░░]  Scripts and scene prompts</code>\n"
                "<code>[░░░░░░░░]  Titles, thumbnails and metadata</code>",
            )
            try:
                jobs = create_custom_jobs(brief, get_publish_target(), parts=count)
                cards = "\n".join(
                    f"  {_STATUS_EMOJI.get(job.status, '⚪')} <code>{job.id[:8]}</code> {html.escape(job.topic)}"
                    for job in jobs
                )
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    f"✅ <b>{count}-part custom series planned</b>\n"
                    f"{_divider()}\n\n"
                    f"{cards}\n\n"
                    "📝 Scripts, scene direction, titles, thumbnail prompts and hashtags "
                    "are attached to each job.\n"
                    "🛡️ <b>Publishing remains blocked until preview review and approval.</b>",
                    _keyboard(),
                )
                progress_message_id = None
            except Exception as exc:
                logger.exception("Custom creator failed")
                await finish_progress_message(
                    chat_id,
                    progress_message_id,
                    f"⚠️ Could not create the custom series: {_safe_error_text(exc)}",
                    _keyboard(),
                )
                progress_message_id = None
            return

        if chat_id in _pending_account_name:
            platform = _pending_account_name.pop(chat_id)
            try:
                from .platform_accounts import add_account

                account = add_account(platform, text)
                await send_message(
                    chat_id,
                    f"✅ <b>{platform.title()} account slot added</b>\n"
                    f"{html.escape(str(account['name']))}\n\n"
                    "The slot is disabled until you connect and verify the real account. "
                    "OAuth/publishing remains approval-gated.",
                    _account_manager_keyboard(),
                )
            except (ValueError, RuntimeError) as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}",
                    _account_manager_keyboard(),
                )
            return

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

        if chat_id in _pending_fish_audio_reference_id:
            if text == "/cancel":
                _pending_fish_audio_reference_id.discard(chat_id)
                await send_message(chat_id, "↩️ Fish Audio reference setup cancelled.", _provider_keyboard())
                return
            try:
                set_fish_audio_reference_id(text)
                _pending_fish_audio_reference_id.discard(chat_id)
                await send_message(
                    chat_id,
                    "✅ <b>Fish Audio reference voice saved.</b>\n"
                    "Add the API key under Credentials, then select Fish Audio under Voice. "
                    "It remains blocked while no-paid mode is ON.",
                    _provider_role_keyboard("voice"),
                )
            except ValueError as exc:
                await send_message(
                    chat_id,
                    f"⚠️ {html.escape(str(exc))}\nSend the reference ID or /cancel.",
                    _back_keyboard(),
                )
            return


        if chat_id in _pending_minimax_voice_id:
            if text == "/cancel":
                _pending_minimax_voice_id.discard(chat_id)
                await send_message(chat_id, "↩️ MiniMax voice setup cancelled.", _provider_keyboard())
                return
            try:
                set_minimax_voice_id(text)
                _pending_minimax_voice_id.discard(chat_id)
                await send_message(
                    chat_id,
                    "✅ <b>MiniMax speech voice saved.</b>\n"
                    "Add the MiniMax Platform API key, then select MiniMax under Voice.",
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
                if provider not in {"huggingface", "fish_audio"}:
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
                    else "Set the reference voice under Controls → Providers → Voice. "
                    "Fish Audio remains blocked while no-paid mode is ON."
                    if provider == "fish_audio"
                    else "Higgsfield uses a Key ID:Key Secret credential for Wan 2.6 "
                    "video generation. Paid generation remains blocked until no-paid mode is disabled."
                    if provider == "higgsfield"
                    else "Media generation will use this provider when AI media is enabled."
                    if PROVIDER_INFO.get(provider, {}).get("wired")
                    else "Its key is stored safely. The generation endpoint for this provider "
                    "gets connected when you activate the account — you can switch back to a "
                    "connected provider here at any time."
                )
                provider_saved_message = (
                    "✅ <b>Hugging Face key saved (encrypted)</b>"
                    if provider == "huggingface"
                    else "✅ <b>Fish Audio key saved (encrypted)</b>"
                    if provider == "fish_audio"
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

        elif text == "/manager" or text.startswith("/manager "):
            from .manager import process_manager_message

            manager_request = text[len("/manager"):].strip()
            if not manager_request:
                await send_message(
                    chat_id,
                    "🧭 <b>Factory Manager</b>\n"
                    "Try <code>status</code>, <code>queue</code>, <code>workers</code>, "
                    "<code>providers</code>, <code>retry &lt;job-id&gt;</code>, "
                    "<code>approve &lt;job-id&gt;</code>, <code>publish &lt;job-id&gt;</code>, "
                    "<code>pause</code>, <code>resume</code>, or "
                    "<code>create a job about &lt;topic&gt;</code>.",
                    _keyboard(),
                )
                return
            reply = process_manager_message(
                manager_request,
                owner_id=str(user_id),
                channel="telegram",
            )
            await send_message(chat_id, reply, _keyboard())

        elif text.lower().startswith("manager ") or text.lower().startswith("manager:"):
            from .manager import process_manager_message

            manager_request = re.sub(r"^manager\s*:?\s*", "", text, flags=re.IGNORECASE)
            reply = process_manager_message(
                manager_request,
                owner_id=str(user_id),
                channel="telegram",
            )
            await send_message(chat_id, reply, _keyboard())

        elif text == "/help":
            await send_message(chat_id, _build_help_message(), _keyboard())

        elif text == "/status":
            await send_message(chat_id, _build_status_message(), _keyboard())

        elif text == "/health":
            await send_message(chat_id, _build_health_message(), _keyboard())

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
            update_id = update.get("update_id")
            request_id = f"telegram:{update_id}" if isinstance(update_id, int) else None
            job = create_job(topic, get_publish_target(), request_id=request_id)
            if job.status == "queued" and job.stage == "topic":
                task = asyncio.create_task(
                    _draft_script_with_progress(chat_id, job.id)
                )
                _track_background_task(task)
                return
            await send_message(
                chat_id,
                (
                    f"ℹ️ <b>This request was already processed</b>\n{_divider()}\n\n"
                    f"{_format_job_card(job)}\n\n"
                    "No duplicate generation was started."
                ),
                _job_keyboard(job),
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
