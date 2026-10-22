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
from .config import get_settings
from .db import FactorySetting, SessionLocal

# ── Niche ──────────────────────────────────────────────────────────────
NICHE_PRESETS: dict[str, str] = {
    "hindi_facts": "Hindi educational facts explainer",
    "hindi_science": "Hindi science explainer",
    "hindi_history": "Hindi history & untold stories explainer",
    "hindi_money": "Hindi money & business explainer",
    "hindi_motivation": "Hindi motivation & life lessons",
}
DEFAULT_NICHE_KEY = "hindi_facts"

# ── Formats ────────────────────────────────────────────────────────────
FORMATS: dict[str, dict[str, object]] = {
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
DEFAULT_FORMAT_KEY = "short"

DAILY_LIMIT_MIN = 1
DAILY_LIMIT_MAX = 20
DAILY_LIMIT_PRESETS = (1, 2, 3, 4, 6, 10)


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
    return FORMATS[get_format_key()]


def set_format(key: str) -> dict[str, object]:
    if key not in FORMATS:
        raise ValueError("Unknown format")
    _set("default_format", key)
    return FORMATS[key]


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
