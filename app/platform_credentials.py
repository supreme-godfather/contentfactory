"""Encrypted platform credentials managed from Telegram."""

from .db import FactorySetting, ProviderCredential, SessionLocal
from .provider_credentials import _cipher


def set_platform_secret(platform: str, secret: str) -> None:
    if platform != "instagram":
        raise ValueError("Unsupported platform credential")
    if not secret.strip() or len(secret.strip()) > 8192:
        raise ValueError("Platform credential must contain 1-8192 characters")
    encrypted = _cipher().encrypt(secret.strip().encode()).decode()
    key = f"platform:{platform}"
    with SessionLocal() as session:
        credential = session.get(ProviderCredential, key)
        if credential is None:
            session.add(ProviderCredential(provider=key, secret_encrypted=encrypted))
        else:
            credential.secret_encrypted = encrypted
        session.commit()


def get_platform_secret(platform: str) -> str | None:
    if platform != "instagram":
        return None
    with SessionLocal() as session:
        credential = session.get(ProviderCredential, f"platform:{platform}")
        if credential is None:
            return None
        return _cipher().decrypt(credential.secret_encrypted.encode()).decode()


def set_platform_value(platform: str, key: str, value: str) -> None:
    if platform != "instagram" or key != "user_id":
        raise ValueError("Unsupported platform setting")
    if not value.strip().isdecimal():
        raise ValueError("Instagram user ID must contain digits only")
    setting_key = f"platform:{platform}:{key}"
    with SessionLocal() as session:
        setting = session.get(FactorySetting, setting_key)
        if setting is None:
            session.add(FactorySetting(key=setting_key, value=value.strip()))
        else:
            setting.value = value.strip()
        session.commit()


def get_platform_value(platform: str, key: str) -> str | None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"platform:{platform}:{key}")
        return setting.value if setting is not None else None


def platform_enabled(platform: str, default: bool = False) -> bool:
    value = get_platform_value(platform, "enabled")
    return value == "true" if value is not None else default


def set_platform_enabled(platform: str, enabled: bool) -> None:
    if platform != "instagram":
        raise ValueError("Unsupported platform")
    set_platform_value(platform, "enabled", str(enabled).lower())
