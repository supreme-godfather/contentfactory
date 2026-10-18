import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from .config import get_settings
from .db import FactorySetting, OAuthCredential, SessionLocal

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube.readonly "
    "https://www.googleapis.com/auth/yt-analytics.readonly"
)


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc


def _redirect_uri() -> str:
    settings = get_settings()
    uri = settings.youtube_redirect_uri or f"{settings.public_base_url.rstrip('/')}/oauth/youtube/callback"
    if not uri.startswith("https://") and settings.app_env == "production":
        raise RuntimeError("YouTube redirect URI must use HTTPS in production")
    return uri


def _sign(value: str) -> str:
    secret = get_settings().token_encryption_key.encode("utf-8")
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_authorization_url() -> str:
    settings = get_settings()
    if not settings.youtube_client_id:
        raise RuntimeError("YOUTUBE_CLIENT_ID is not configured")
    nonce = secrets.token_urlsafe(32)
    expires = int(time.time()) + 600
    state_value = f"{nonce}.{expires}"
    state = f"{state_value}.{_sign(state_value)}"
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "youtube_oauth_state")
        if setting is None:
            session.add(FactorySetting(key="youtube_oauth_state", value=state))
        else:
            setting.value = state
        session.commit()
    query = {
        "client_id": settings.youtube_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": YOUTUBE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(query)}"


def _validate_state(state: str) -> None:
    parts = state.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid OAuth state")
    value, expires_text, signature = parts
    if not hmac.compare_digest(signature, _sign(f"{value}.{expires_text}")):
        raise ValueError("Invalid OAuth state signature")
    if int(expires_text) < int(time.time()):
        raise ValueError("OAuth state expired")
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "youtube_oauth_state")
        if not setting or not hmac.compare_digest(setting.value, state):
            raise ValueError("OAuth state was not issued by this factory")
        session.delete(setting)
        session.commit()


async def exchange_code(code: str, state: str) -> None:
    _validate_state(state)
    settings = get_settings()
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise RuntimeError("YouTube OAuth client is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        payload = response.json()
    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not refresh_token or not access_token:
        raise RuntimeError("Google did not return the required OAuth tokens")
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    cipher = _fernet()
    with SessionLocal() as session:
        credential = session.get(OAuthCredential, "youtube")
        if credential is None:
            credential = OAuthCredential(
                provider="youtube",
                access_token_encrypted=cipher.encrypt(access_token.encode()).decode(),
                refresh_token_encrypted=cipher.encrypt(refresh_token.encode()).decode(),
            )
            session.add(credential)
        else:
            credential.access_token_encrypted = cipher.encrypt(access_token.encode()).decode()
            credential.refresh_token_encrypted = cipher.encrypt(refresh_token.encode()).decode()
        credential.token_type = payload.get("token_type", "Bearer")
        credential.expires_at = expires_at
        credential.scope = payload.get("scope", YOUTUBE_SCOPES)
        session.commit()


def connection_status() -> dict[str, object]:
    settings = get_settings()
    with SessionLocal() as session:
        credential = session.get(OAuthCredential, "youtube")
    return {
        "configured": bool(settings.youtube_client_id and settings.youtube_client_secret),
        "authorized": credential is not None,
        "expires_at": credential.expires_at.isoformat() if credential and credential.expires_at else None,
        "scopes": credential.scope if credential else None,
    }
