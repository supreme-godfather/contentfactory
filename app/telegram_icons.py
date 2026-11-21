from functools import lru_cache

from sqlalchemy import select

from .db import FactorySetting, SessionLocal

ICON_SLOTS: dict[str, str] = {
    "studio": "✨",
    "review": "✅",
    "analytics": "📈",
    "operations": "🖥",
    "settings": "⚙️",
    "automation": "🤖",
    "original_creation": "✨",
    "custom_series": "🧠",
    "trends": "🔥",
    "clip_studio": "🎞",
    "clip_vod": "➕",
    "clip_live": "🔴",
    "review_queue": "📥",
    "pipeline_status": "📊",
    "quality_review": "🔍",
    "analytics_sync": "🔄",
    "system_health": "🖥",
    "render_worker": "🛠",
    "niche": "🎯",
    "content_format": "🎞",
    "reel_profile": "📸",
    "providers": "🧩",
    "provider_script": "📝",
    "provider_image": "🖼",
    "provider_voice": "🎙",
    "provider_video": "🎬",
    "credentials": "🔐",
    "audio_library": "🎵",
    "accounts": "🔗",
    "publishing_destinations": "📡",
    "daily_limit": "📤",
    "publishing_mode": "🧪",
    "platform_guide": "📖",
    "youtube": "▶️",
    "instagram": "📸",
    "icon_theme": "💎",
    "refresh": "🔄",
    "pause": "⏸️",
    "resume": "▶️",
    "back": "🔙",
    "add": "➕",
    "approve": "✅",
    "reject": "❌",
    "revise": "✍️",
    "publish": "📤",
    "preview": "▶️",
    "retry": "🔁",
    "schedule": "⏰",
    "delete": "🗑",
    "connect": "🔗",
    "verify": "✅",
    "cancel": "❌",
}


@lru_cache(maxsize=1)
def get_icon_configuration() -> tuple[bool, dict[str, str]]:
    with SessionLocal() as session:
        enabled_setting = session.get(FactorySetting, "telegram_ui_icons_enabled")
        settings = list(
            session.scalars(
                select(FactorySetting).where(FactorySetting.key.like("telegram_ui_icon:%"))
            )
        )
    configured = {
        setting.key.split(":", 1)[1]: setting.value
        for setting in settings
        if setting.key.split(":", 1)[1] in ICON_SLOTS and setting.value
    }
    return bool(enabled_setting and enabled_setting.value == "true"), configured


def invalidate_icon_cache() -> None:
    get_icon_configuration.cache_clear()


def set_icons_enabled(enabled: bool) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "telegram_ui_icons_enabled")
        if setting is None:
            session.add(FactorySetting(key="telegram_ui_icons_enabled", value=str(enabled).lower()))
        else:
            setting.value = str(enabled).lower()
        session.commit()
    invalidate_icon_cache()


def set_icon_id(slot: str, custom_emoji_id: str) -> None:
    if slot not in ICON_SLOTS:
        raise ValueError("Unknown premium icon slot")
    value = custom_emoji_id.strip()
    if not value or len(value) > 240 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise ValueError("Custom emoji ID must be a non-empty printable identifier")
    with SessionLocal() as session:
        key = f"telegram_ui_icon:{slot}"
        setting = session.get(FactorySetting, key)
        if setting is None:
            session.add(FactorySetting(key=key, value=value))
        else:
            setting.value = value
        session.commit()
    invalidate_icon_cache()


def clear_icon_id(slot: str) -> None:
    if slot not in ICON_SLOTS:
        raise ValueError("Unknown premium icon slot")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"telegram_ui_icon:{slot}")
        if setting is not None:
            session.delete(setting)
            session.commit()
    invalidate_icon_cache()


def icon_button(slot: str, label: str, **action: str) -> dict[str, str]:
    if slot not in ICON_SLOTS:
        raise ValueError("Unknown premium icon slot")
    enabled, configured = get_icon_configuration()
    button = {"text": label, **action}
    custom_id = configured.get(slot) if enabled else None
    if custom_id:
        button["icon_custom_emoji_id"] = custom_id
    else:
        button["text"] = f"{ICON_SLOTS[slot]} {label}"
    return button


def has_custom_icons(reply_markup: dict | None) -> bool:
    return bool(
        reply_markup
        and any(
            "icon_custom_emoji_id" in button
            for row in reply_markup.get("inline_keyboard", [])
            for button in row
        )
    )


def unicode_fallback_markup(reply_markup: dict | None) -> dict | None:
    if not reply_markup:
        return reply_markup
    _, configured = get_icon_configuration()
    reverse = {custom_id: ICON_SLOTS[slot] for slot, custom_id in configured.items()}
    rows = []
    for row in reply_markup.get("inline_keyboard", []):
        rendered_row = []
        for source in row:
            button = dict(source)
            custom_id = button.pop("icon_custom_emoji_id", None)
            if custom_id:
                fallback = reverse.get(custom_id, "•")
                button["text"] = f"{fallback} {button['text']}"
            rendered_row.append(button)
        rows.append(rendered_row)
    return {**reply_markup, "inline_keyboard": rows}
