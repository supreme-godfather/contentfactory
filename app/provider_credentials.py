"""Provider registry and encrypted credential storage.

The owner adds providers from Telegram (the headquarters) with an API key
(owner decision, 2026-10-22): ANY provider can be added and the active one
can be switched at any time — e.g. when a provider runs out of credits or
hits a limit, the owner taps another configured provider and generation
continues with it.

- Keys are stored Fernet-encrypted, never echoed, never logged.
- ``wired`` says whether a generation route for that provider exists in
  this codebase today (Google and OpenAI do). A stored-but-unwired
  provider (Higgsfield, Kling, Runway, fal.ai, or a custom one) keeps its
  key ready and selected; media generation safely falls back to "no AI
  media" for it instead of crashing, until its endpoint is connected.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import httpx

from .config import get_settings
from .db import FactorySetting, ProviderCredential, SessionLocal

logger = logging.getLogger(__name__)
PROVIDER_COOLDOWN_DEFAULT_SECONDS = 1800
PROVIDER_QUOTA_COOLDOWN_SECONDS = 43200
PROVIDER_COOLDOWN_MAX_SECONDS = 86400

# name -> label shown in Telegram, the secret the owner should send, wired?
PROVIDER_INFO: dict[str, dict[str, object]] = {
    "google": {
        "label": "☁️ Google (Imagen / Text-to-Speech)",
        "secret_label": "Google service-account JSON",
        "wired": True,
    },
    "openai": {
        "label": "🤖 OpenAI",
        "secret_label": "OpenAI API key",
        "wired": True,
    },
    "gemini": {
        "label": "✨ Google AI Studio (Gemini)",
        "secret_label": "Google AI Studio API key",
        "wired": True,
    },
    "huggingface": {
        "label": "🤗 Hugging Face model access",
        "secret_label": "Hugging Face user access token",
        "wired": True,
    },
    "minimax": {
        "label": "🎞️ MiniMax H3 video",
        "secret_label": "MiniMax API key",
        "wired": True,
    },
    "elevenlabs": {
        "label": "🗣️ ElevenLabs Text to Speech",
        "secret_label": "ElevenLabs API key",
        "wired": True,
    },
    "higgsfield": {
        "label": "🎬 Higgsfield",
        "secret_label": "Higgsfield API key",
        "wired": False,
    },
    "kling": {
        "label": "🎥 Kling AI",
        "secret_label": "Kling API key",
        "wired": False,
    },
    "runway": {
        "label": "🛩️ Runway",
        "secret_label": "Runway API key",
        "wired": False,
    },
    "fal": {
        "label": "⚡ fal.ai (FLUX / Veo / many models)",
        "secret_label": "fal.ai API key",
        "wired": False,
    },
    "custom": {
        "label": "🧩 Any other provider",
        "secret_label": "API key for your provider",
        "wired": False,
    },
}

SUPPORTED_PROVIDERS = tuple(PROVIDER_INFO.keys())

PROVIDER_ROLES: dict[str, dict[str, str]] = {
    "script": {
        "template": "Built-in Hindi script",
        "gemini": "Gemini 3.5 Flash-Lite (AI Studio)",
        "huggingface": "Hugging Face Inference Providers",
        "openai": "OpenAI GPT-4o mini",
    },
    "image": {
        "google": "Google Imagen",
        "openai": "OpenAI Images",
        "huggingface": "FLUX.1 Schnell (Hugging Face GPU endpoint)",
        "off": "No AI image",
    },
    "voice": {
        "google": "Google Text-to-Speech",
        "edge_tts": "Microsoft Edge TTS (online)",
        "elevenlabs": "ElevenLabs (credit-based)",
        "openai": "OpenAI Text-to-Speech",
        "off": "No AI narration",
    },
    "video": {
        "minimax": "MiniMax H3 text-to-video",
        "off": "No AI video",
    },
}


def _cipher() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode("ascii"))


def set_provider_secret(provider: str, secret: str) -> None:
    if provider not in PROVIDER_INFO:
        raise ValueError("Unsupported media provider")
    if not secret.strip():
        raise ValueError("Provider secret cannot be empty")
    encrypted = _cipher().encrypt(secret.strip().encode()).decode()
    with SessionLocal() as session:
        credential = session.get(ProviderCredential, provider)
        if credential is None:
            session.add(ProviderCredential(provider=provider, secret_encrypted=encrypted))
        else:
            credential.secret_encrypted = encrypted
        session.commit()


def get_provider_secret(provider: str) -> str | None:
    with SessionLocal() as session:
        credential = session.get(ProviderCredential, provider)
        if credential is None:
            return None
        return _cipher().decrypt(credential.secret_encrypted.encode()).decode()


def provider_status() -> dict[str, object]:
    """Configured flag per registered provider + the selected one."""
    settings = get_settings()
    with SessionLocal() as session:
        configured = {
            name: session.get(ProviderCredential, name) is not None
            for name in PROVIDER_INFO
        }
        selected = session.get(FactorySetting, "media_provider")
        selected_name = selected.value if selected else "google"
    if selected_name not in PROVIDER_INFO:
        selected_name = "google"
    status: dict[str, object] = {"selected": selected_name}
    for name, is_configured in configured.items():
        status[name] = is_configured
        cooldown = provider_cooldown_until(name) if name in {
            "google", "openai", "gemini", "huggingface", "elevenlabs", "minimax"
        } else None
        status[f"{name}_cooldown_until"] = cooldown.isoformat() if cooldown else None
    # Google can also be configured purely through environment variables.
    status["google"] = bool(configured["google"] or settings.google_service_account_json)
    status["elevenlabs_voice_id"] = bool(get_elevenlabs_voice_id())
    status["huggingface_image_endpoint"] = bool(get_huggingface_image_endpoint())
    status["minimax"] = bool(configured["minimax"] or settings.minimax_api_key)
    return status


def select_provider(provider: str) -> None:
    if provider not in PROVIDER_INFO:
        raise ValueError("Unsupported media provider")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "media_provider")
        if setting is None:
            session.add(FactorySetting(key="media_provider", value=provider))
        else:
            setting.value = provider
        session.commit()


def get_provider_for_role(role: str) -> str:
    """Return a role-specific provider, falling back to the legacy selection."""
    if role not in PROVIDER_ROLES:
        raise ValueError("Unsupported provider role")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"provider_{role}")
        if setting is not None and setting.value in PROVIDER_ROLES[role]:
            return setting.value
        legacy = session.get(FactorySetting, "media_provider")
        selected = legacy.value if legacy else get_settings().media_provider
    if role == "script":
        return selected if selected in {"openai", "gemini", "huggingface"} else "template"
    if role == "video":
        return selected if selected == "minimax" else "off"
    if selected in {"google", "openai"}:
        return selected
    return "off"


def is_provider_quota_error(error: Exception) -> bool:
    if not isinstance(error, httpx.HTTPStatusError):
        return False
    status_code = error.response.status_code
    if status_code in {402, 429}:
        return True
    if status_code != 403:
        return False
    try:
        payload = error.response.json()
    except ValueError:
        payload = {}
    details = str(payload).lower()
    compact_details = re.sub(r"[^a-z0-9]", "", details)
    return any(
        marker in compact_details
        for marker in (
            "quota",
            "resourceexhausted",
            "ratelimit",
            "billing",
            "insufficientquota",
        )
    )


def mark_provider_quota_error(provider: str, error: Exception) -> datetime:
    if provider not in {"google", "openai", "gemini", "huggingface", "elevenlabs", "minimax"}:
        raise ValueError("Unsupported wired provider")
    retry_after = PROVIDER_COOLDOWN_DEFAULT_SECONDS
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code == 402:
            retry_after = PROVIDER_QUOTA_COOLDOWN_SECONDS
        else:
            try:
                body = str(error.response.json()).lower()
            except ValueError:
                body = ""
            compact_body = re.sub(r"[^a-z0-9]", "", body)
            if any(
                marker in compact_body
                for marker in ("insufficientquota", "resourceexhausted", "quotaexceeded", "billing")
            ):
                retry_after = PROVIDER_QUOTA_COOLDOWN_SECONDS
        raw_retry_after = error.response.headers.get("Retry-After")
        try:
            retry_after = int(raw_retry_after) if raw_retry_after else retry_after
        except ValueError:
            pass
    retry_after = max(60, min(retry_after, PROVIDER_COOLDOWN_MAX_SECONDS))
    until = datetime.now(timezone.utc) + timedelta(seconds=retry_after)
    with SessionLocal() as session:
        setting_key = f"provider_cooldown:{provider}"
        setting = session.get(FactorySetting, setting_key)
        if setting is None:
            session.add(FactorySetting(key=setting_key, value=until.isoformat()))
        else:
            setting.value = until.isoformat()
        session.commit()
    return until


def provider_cooldown_until(provider: str) -> datetime | None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"provider_cooldown:{provider}")
        if setting is None:
            return None
        raw_value = setting.value
        try:
            until = datetime.fromisoformat(raw_value)
        except ValueError:
            session.delete(setting)
            session.commit()
            return None
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if until <= datetime.now(timezone.utc):
            session.delete(setting)
            session.commit()
            return None
        return until


def provider_candidates_for_role(role: str) -> list[str]:
    """Return configured, non-cooled providers in primary-then-fallback order."""
    primary = get_provider_for_role(role)
    if no_paid_providers_enabled():
        if role == "image":
            return ["off"]
        if role == "video":
            return ["off"]
        if role == "voice" and primary == "edge_tts":
            return [primary]
        if primary == "off":
            return [primary]
        return []
    if primary == "elevenlabs":
        status = provider_status()
        if not status.get("elevenlabs") or not get_elevenlabs_voice_id():
            return []
        if provider_cooldown_until(primary) is not None:
            return []
        return [primary]
    if primary == "huggingface":
        status = provider_status()
        if not status.get("huggingface"):
            return []
        if role == "image" and not status.get("huggingface_image_endpoint"):
            return []
        if provider_cooldown_until(primary) is not None:
            return []
        return [primary]
    if primary == "minimax":
        status = provider_status()
        if not status.get("minimax"):
            return []
        if provider_cooldown_until(primary) is not None:
            return []
        return [primary]
    if primary not in {"google", "openai"}:
        return [primary]
    fallback = "openai" if primary == "google" else "google"
    status = provider_status()
    candidates = []
    for provider in (primary, fallback):
        configured = bool(status.get(provider))
        if provider == "google":
            configured = configured and bool(get_settings().google_cloud_project)
        if configured and provider_cooldown_until(provider) is None:
            candidates.append(provider)
    return candidates


def set_provider_for_role(role: str, provider: str) -> None:
    if role not in PROVIDER_ROLES or provider not in PROVIDER_ROLES[role]:
        raise ValueError("Unsupported provider role or provider")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"provider_{role}")
        if setting is None:
            session.add(FactorySetting(key=f"provider_{role}", value=provider))
        else:
            setting.value = provider
        session.commit()


def get_huggingface_image_endpoint() -> str:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "huggingface_image_endpoint")
        return setting.value.strip() if setting and setting.value else ""


def set_huggingface_image_endpoint(value: str) -> str:
    try:
        endpoint = httpx.URL(value.strip())
    except httpx.InvalidURL as exc:
        raise ValueError("Endpoint must be a valid HTTPS Hugging Face Inference Endpoint URL.") from exc
    host = (endpoint.host or "").lower().rstrip(".")
    if (
        endpoint.scheme != "https"
        or not host.endswith(".endpoints.huggingface.cloud")
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in {"", "/"}
    ):
        raise ValueError(
            "Use the HTTPS base URL ending in .endpoints.huggingface.cloud, without a path or query."
        )
    normalized = str(endpoint).rstrip("/")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "huggingface_image_endpoint")
        if setting is None:
            session.add(FactorySetting(key="huggingface_image_endpoint", value=normalized))
        else:
            setting.value = normalized
        session.commit()
    return normalized


def media_generation_enabled() -> bool:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "media_generation_enabled")
        if setting is not None:
            return setting.value == "true"
    return get_settings().media_generation_enabled or get_settings().google_media_enabled


def set_media_generation_enabled(enabled: bool) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "media_generation_enabled")
        if setting is None:
            session.add(FactorySetting(key="media_generation_enabled", value=str(enabled).lower()))
        else:
            setting.value = str(enabled).lower()
        session.commit()


def get_elevenlabs_voice_id() -> str:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "elevenlabs_voice_id")
        return setting.value.strip() if setting and setting.value else ""


def set_elevenlabs_voice_id(voice_id: str) -> None:
    value = voice_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Voice ID must contain only letters, numbers, underscores, or hyphens.")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "elevenlabs_voice_id")
        if setting is None:
            session.add(FactorySetting(key="elevenlabs_voice_id", value=value))
        else:
            setting.value = value
        session.commit()


def no_paid_providers_enabled() -> bool:
    """Block metered providers unless the owner explicitly opts out."""
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "no_paid_providers")
        return setting is None or setting.value == "true"


def set_no_paid_providers_enabled(enabled: bool) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "no_paid_providers")
        if setting is None:
            session.add(FactorySetting(key="no_paid_providers", value=str(enabled).lower()))
        else:
            setting.value = str(enabled).lower()
        session.commit()
