"""Headquarters settings: the runtime controls the owner runs from Telegram.

Telegram is the factory's headquarters (owner decision, 2026-10-22), so the
things the owner used to need a redeploy for now live here as persisted
runtime settings in the `factory_settings` table:

- **Niche** — what the channel makes. Presets for now; the scriptwriter and
  generated metadata read the current niche on every job.
- **Default format** — Shorts (~60s, 9:16) or Long (~4 min, 16:9). Both are
  supported; Telegram chooses which one new jobs are generated in.
- **Daily upload limit** — how many videos may publish per day. The publish
  path enforces the runtime value when one is set, else the env default.

Every getter falls back to the configured default when nothing has been
set from Telegram yet, so behaviour before the owner touches a control is
exactly the old behaviour.
"""
import re

from .config import get_settings
from .db import FactorySetting, SessionLocal

# ── Niche ──────────────────────────────────────────────────────────────
NICHE_PRESETS: dict[str, str] = {
    "hindi_facts": "Hindi educational facts explainer",
    "hindi_science": "Hindi science explainer",
    "hindi_history": "Hindi history & untold stories explainer",
    "hindi_money": "Hindi money & business explainer",
    "hindi_motivation": "Hindi motivation & life lessons",
    "hindi_dark_psychology": "Hindi dark psychology, mindset & history",
}
DEFAULT_NICHE_KEY = "hindi_dark_psychology"

# ── Formats ────────────────────────────────────────────────────────────
FORMATS: dict[str, dict[str, object]] = {
    "reel": {
        "label": "Instagram Reel (vertical, 45–60s)",
        "format_code": "instagram_reel",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 55,
        "script_words": "105-140",
    },
    "short": {
        "label": "Shorts (vertical, ~60s)",
        "format_code": "vertical_short",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 60,
        "script_words": "130-150",
    },
    "long": {
        "label": "Long-form (landscape, ~4 min)",
        "format_code": "long_form",
        "aspect_ratio": "16:9",
        "target_duration_seconds": 240,
        "script_words": "550-700",
    },
}
DEFAULT_FORMAT_KEY = "reel"

PUBLISH_TARGETS = {
    "instagram": "Instagram only",
    "youtube": "YouTube only",
    "both": "YouTube + Instagram",
}

DAILY_LIMIT_MIN = 1
DAILY_LIMIT_MAX = 20
DAILY_LIMIT_PRESETS = (1, 2, 3, 4, 6, 10)
REEL_DURATION_PRESETS = (45, 55, 60)
REEL_VOICE_PRESETS = (
    "hi-IN-Neural2-A",
    "hi-IN-Neural2-B",
    "hi-IN-Wavenet-A",
    "hi-IN-Wavenet-B",
)


def _get(key: str) -> str | None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, key)
        return setting.value if setting is not None else None


def _set(key: str, value: str) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, key)
        if setting is None:
            session.add(FactorySetting(key=key, value=value))
        else:
            setting.value = value
        session.commit()


def get_niche_key() -> str:
    key = _get("niche")
    return key if key in NICHE_PRESETS else DEFAULT_NICHE_KEY


def get_niche_label() -> str:
    return NICHE_PRESETS[get_niche_key()]


def set_niche(key: str) -> str:
    if key not in NICHE_PRESETS:
        raise ValueError("Unknown niche")
    _set("niche", key)
    return NICHE_PRESETS[key]


def get_format_key() -> str:
    key = _get("default_format")
    return key if key in FORMATS else DEFAULT_FORMAT_KEY


def get_format() -> dict[str, object]:
    key = get_format_key()
    info = dict(FORMATS[key])
    if key == "reel":
        info["target_duration_seconds"] = get_reel_duration()
    return info


def set_format(key: str) -> dict[str, object]:
    if key not in FORMATS:
        raise ValueError("Unknown format")
    _set("default_format", key)
    return FORMATS[key]


def get_publish_target() -> str:
    target = _get("publish_target")
    if target in PUBLISH_TARGETS:
        return target
    configured_target = get_settings().automation_platform
    return configured_target if configured_target in PUBLISH_TARGETS else "instagram"


def set_publish_target(target: str) -> str:
    if target not in PUBLISH_TARGETS:
        raise ValueError("Unsupported publishing destination")
    _set("publish_target", target)
    return target


def get_render_worker_target() -> str:
    return "railway-renderer"


def set_render_worker_target(target: str) -> str:
    value = target.strip()
    if value not in {"auto", "railway-renderer"}:
        raise ValueError("Only the Railway render worker is enabled")
    _set("render_worker_target", "railway-renderer")
    return "railway-renderer"


def get_daily_publish_limit() -> int:
    """Runtime limit from Telegram when set, else the env default."""
    raw = _get("max_daily_publications")
    if raw is not None:
        try:
            return max(DAILY_LIMIT_MIN, min(DAILY_LIMIT_MAX, int(raw)))
        except ValueError:
            pass
    return get_settings().max_daily_publications


def set_daily_publish_limit(limit: int) -> int:
    clamped = max(DAILY_LIMIT_MIN, min(DAILY_LIMIT_MAX, int(limit)))
    _set("max_daily_publications", str(clamped))
    return clamped


def is_mock_publishing() -> bool:
    value = _get("mock_publishing")
    if value is not None:
        return value == "true"
    return get_settings().mock_publishing


def set_mock_publishing(enabled: bool) -> None:
    _set("mock_publishing", str(enabled).lower())


def get_reel_duration() -> int:
    raw = _get("reel_duration_seconds")
    try:
        value = int(raw) if raw is not None else get_settings().reel_default_duration_seconds
    except ValueError:
        value = get_settings().reel_default_duration_seconds
    return min(REEL_DURATION_PRESETS, key=lambda preset: abs(preset - value))


def set_reel_duration(seconds: int) -> int:
    if seconds not in REEL_DURATION_PRESETS:
        raise ValueError("Unsupported Reel duration")
    _set("reel_duration_seconds", str(seconds))
    return seconds


def get_reel_voice() -> str:
    voice = _get("reel_voice")
    if voice in REEL_VOICE_PRESETS:
        return voice
    return get_settings().google_tts_voice


def set_reel_voice(voice: str) -> str:
    if voice not in REEL_VOICE_PRESETS:
        raise ValueError("Unsupported Hindi voice")
    _set("reel_voice", voice)
    return voice
