from cryptography.fernet import Fernet

from .config import get_settings
from .db import FactorySetting, ProviderCredential, SessionLocal


def _cipher() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode("ascii"))


def set_provider_secret(provider: str, secret: str) -> None:
    if provider not in {"google", "openai"}:
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
    settings = get_settings()
    with SessionLocal() as session:
        configured = {
            name: session.get(ProviderCredential, name) is not None
            for name in ("google", "openai")
        }
        selected = session.get(FactorySetting, "media_provider")
        selected_name = selected.value if selected else "google"
    return {
        "selected": selected_name,
        "google": configured["google"] or bool(settings.google_service_account_json),
        "openai": configured["openai"],
    }


def select_provider(provider: str) -> None:
    if provider not in {"google", "openai"}:
        raise ValueError("Unsupported media provider")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "media_provider")
        if setting is None:
            session.add(FactorySetting(key="media_provider", value=provider))
        else:
            setting.value = provider
        session.commit()
