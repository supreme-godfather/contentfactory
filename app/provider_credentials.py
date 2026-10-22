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

from cryptography.fernet import Fernet

from .config import get_settings
from .db import FactorySetting, ProviderCredential, SessionLocal

logger = logging.getLogger(__name__)

# name -> label shown in Telegram, the secret the owner should send, wired?
PROVIDER_INFO: dict[str, dict[str, object]] = {
    "google": {
        "label": "☁️ Google (Veo / Imagen / TTS)",
        "secret_label": "Google service-account JSON",
        "wired": True,
    },
    "openai": {
        "label": "🤖 OpenAI",
        "secret_label": "OpenAI API key",
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
    # Google can also be configured purely through environment variables.
    status["google"] = bool(configured["google"] or settings.google_service_account_json)
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
