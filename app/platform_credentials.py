"""Encrypted platform credentials managed from Telegram."""

import httpx

from .config import get_settings
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


async def exchange_instagram_token(short_lived_token: str) -> int | None:
    token = short_lived_token.strip()
    if not token or len(token) > 8192:
        raise ValueError("Instagram token must contain 1-8192 characters")
    settings = get_settings()
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise RuntimeError("META_APP_ID and META_APP_SECRET must be configured in Railway")
    graph_version = settings.instagram_graph_version or "v21.0"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"https://graph.facebook.com/{graph_version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": token,
            },
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Meta returned an invalid token exchange response") from exc
    long_lived_token = payload.get("access_token")
    if not isinstance(long_lived_token, str) or not long_lived_token:
        raise RuntimeError("Meta did not return a long-lived Instagram token")
    set_platform_secret("instagram", long_lived_token)
    expires_in = payload.get("expires_in")
    return int(expires_in) if expires_in is not None else None


def remove_platform_account(platform: str) -> bool:
    if platform != "instagram":
        raise ValueError("Unsupported platform account")
    removed = False
    with SessionLocal() as session:
        credential = session.get(ProviderCredential, "platform:instagram")
        if credential is not None:
            session.delete(credential)
            removed = True
        user_id_setting = session.get(FactorySetting, "platform:instagram:user_id")
        if user_id_setting is not None:
            session.delete(user_id_setting)
            removed = True
        enabled_setting = session.get(FactorySetting, "platform:instagram:enabled")
        if enabled_setting is None:
            session.add(FactorySetting(key="platform:instagram:enabled", value="false"))
        else:
            if enabled_setting.value != "false":
                removed = True
            enabled_setting.value = "false"
        session.commit()
    return removed


def verify_instagram_account() -> dict[str, str]:
    settings = get_settings()
    user_id = get_platform_value("instagram", "user_id") or settings.instagram_user_id
    access_token = get_platform_secret("instagram") or settings.instagram_access_token
    if not user_id or not user_id.isdecimal() or not access_token:
        raise RuntimeError("Instagram account ID or access token is not configured")
    graph_version = settings.instagram_graph_version or "v21.0"
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"https://graph.facebook.com/{graph_version}/{user_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id,username"},
        )
        response.raise_for_status()
        account = response.json()
    if str(account.get("id")) != user_id:
        raise RuntimeError("Meta returned a different Instagram account")
    return {
        "id": user_id,
        "username": str(account.get("username") or ""),
    }


def set_platform_value(platform: str, key: str, value: str) -> None:
    if platform not in {"instagram", "youtube"} or key not in {"user_id", "enabled"}:
        raise ValueError("Unsupported platform setting")
    if platform == "youtube" and key != "enabled":
        raise ValueError("Unsupported platform setting")
    normalized = value.strip().lower() if key == "enabled" else value.strip()
    if key == "user_id" and not normalized.isdecimal():
        raise ValueError("Instagram user ID must contain digits only")
    if key == "enabled" and normalized not in {"true", "false"}:
        raise ValueError("Platform enabled value must be true or false")
    setting_key = f"platform:{platform}:{key}"
    with SessionLocal() as session:
        setting = session.get(FactorySetting, setting_key)
        if setting is None:
            session.add(FactorySetting(key=setting_key, value=normalized))
        else:
            setting.value = normalized
        session.commit()


def get_platform_value(platform: str, key: str) -> str | None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, f"platform:{platform}:{key}")
        return setting.value if setting is not None else None


def platform_enabled(platform: str, default: bool = False) -> bool:
    if platform not in {"instagram", "youtube"}:
        raise ValueError("Unsupported platform")
    value = get_platform_value(platform, "enabled")
    return value == "true" if value is not None else default


def set_platform_enabled(platform: str, enabled: bool) -> None:
    if platform not in {"instagram", "youtube"}:
        raise ValueError("Unsupported platform")
    set_platform_value(platform, "enabled", str(enabled).lower())
