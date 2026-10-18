import base64
import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, timedelta, timezone
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


def get_valid_access_token() -> str:
    """Return a usable access token, refreshing it shortly before expiry."""
    settings = get_settings()
    cipher = _fernet()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        credential = session.get(OAuthCredential, "youtube")
        if credential is None:
            raise RuntimeError("YouTube is not authorized")
        if credential.expires_at and credential.expires_at > now + timedelta(seconds=60):
            return cipher.decrypt(credential.access_token_encrypted.encode()).decode()
        refresh_token = cipher.decrypt(credential.refresh_token_encrypted.encode()).decode()

    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise RuntimeError("YouTube OAuth client is not configured")
    with httpx.Client(timeout=20) as client:
        response = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError("Google did not return a refreshed access token")
    expires_at = now + timedelta(seconds=int(payload.get("expires_in", 3600)))
    with SessionLocal() as session:
        credential = session.get(OAuthCredential, "youtube")
        if credential is None:
            raise RuntimeError("YouTube authorization was removed")
        credential.access_token_encrypted = cipher.encrypt(access_token.encode()).decode()
        credential.expires_at = expires_at
        credential.token_type = payload.get("token_type", credential.token_type)
        session.commit()
    return access_token


def sync_channel_analytics() -> dict[str, object]:
    """Fetch official channel totals and persist one current analytics snapshot."""
    from .discovery import add_analytics_snapshot

    settings = get_settings()
    token = get_valid_access_token()
    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, settings.youtube_analytics_days))
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30) as client:
        channel_response = client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers=headers,
        )
        channel_response.raise_for_status()
        channels = channel_response.json().get("items", [])
        if not channels:
            raise RuntimeError("No YouTube channel is available for the authorized account")
        channel = channels[0]
        channel_id = channel.get("id", "mine")
        account = channel.get("snippet", {}).get("title") or channel_id
        analytics_response = client.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params={
                "ids": "channel==MINE",
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "metrics": "views,likes,comments,estimatedMinutesWatched",
            },
            headers=headers,
        )
        analytics_response.raise_for_status()
        rows = analytics_response.json().get("rows", [])
    values = rows[0] if rows else [0, 0, 0, 0]
    snapshot = add_analytics_snapshot(
        platform="youtube",
        account=account,
        content_id=f"channel:{channel_id}",
        views=int(values[0] or 0),
        likes=int(values[1] or 0),
        comments=int(values[2] or 0),
        shares=0,
        watch_seconds=int(float(values[3] or 0) * 60),
    )
    return {
        "platform": "youtube",
        "account": account,
        "channel_id": channel_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "snapshot_id": snapshot.id,
    }
