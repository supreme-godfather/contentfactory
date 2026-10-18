"""Tests for the YouTube OAuth flow and security helpers."""

import base64
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.db import FactorySetting, OAuthCredential, SessionLocal, init_db


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    fernet_key = Fernet.generate_key().decode("ascii")

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", fernet_key)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("YOUTUBE_REDIRECT_URI", "https://factory.example/oauth/youtube/callback")
    monkeypatch.setenv("APP_ENV", "development")

    get_settings.cache_clear()
    import app.db
    from app.db import Base, engine, init_db

    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    get_settings.cache_clear()


def test_connection_status_initially_unauthorized():
    from app.youtube_oauth import connection_status

    status = connection_status()
    assert status["configured"] is True
    assert status["authorized"] is False
    assert status["expires_at"] is None
    assert status["scopes"] is None


def test_create_authorization_url_requires_client_id(monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "")
    get_settings.cache_clear()
    from app.youtube_oauth import create_authorization_url

    with pytest.raises(RuntimeError, match="YOUTUBE_CLIENT_ID is not configured"):
        create_authorization_url()


def test_create_authorization_url_generates_valid_url():
    from app.youtube_oauth import create_authorization_url

    url = create_authorization_url()
    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "client_id=test-client-id" in url
    assert "response_type=code" in url
    assert "state=" in url

    # Verify state was saved to db
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "youtube_oauth_state")
        assert setting is not None
        assert len(setting.value.split(".")) == 3


def test_validate_state_rejects_malformed_state():
    from app.youtube_oauth import _validate_state

    with pytest.raises(ValueError, match="Invalid OAuth state"):
        _validate_state("not-enough-parts")


def test_validate_state_rejects_tampered_signature():
    from app.youtube_oauth import _validate_state, create_authorization_url

    create_authorization_url()
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "youtube_oauth_state")
        valid_state = setting.value

    parts = valid_state.split(".")
    tampered_state = f"{parts[0]}.{parts[1]}.bad-signature"

    with pytest.raises(ValueError, match="Invalid OAuth state signature"):
        _validate_state(tampered_state)


def test_validate_state_rejects_expired_state():
    from app.youtube_oauth import _sign, _validate_state

    past_expires = int(time.time()) - 100
    expired_value = f"nonce.{past_expires}"
    expired_state = f"{expired_value}.{_sign(expired_value)}"

    with pytest.raises(ValueError, match="OAuth state expired"):
        _validate_state(expired_state)


def test_validate_state_rejects_unknown_state():
    from app.youtube_oauth import _sign, _validate_state

    future_expires = int(time.time()) + 300
    val = f"randomnonce.{future_expires}"
    signed_state = f"{val}.{_sign(val)}"

    with pytest.raises(ValueError, match="OAuth state was not issued by this factory"):
        _validate_state(signed_state)


@pytest.mark.asyncio
async def test_exchange_code_success():
    from app.youtube_oauth import create_authorization_url, exchange_code, connection_status, _fernet

    auth_url = create_authorization_url()
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "youtube_oauth_state")
        state = setting.value

    mock_response = AsyncMock()
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {
        "access_token": "mock-access-token-12345",
        "refresh_token": "mock-refresh-token-67890",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/youtube.upload",
    }

    with patch("httpx.AsyncClient.post", return_value=mock_response):
        await exchange_code(code="sample-auth-code", state=state)

    # State should be consumed (single use)
    with SessionLocal() as session:
        assert session.get(FactorySetting, "youtube_oauth_state") is None

        # Credential should be encrypted in DB
        cred = session.get(OAuthCredential, "youtube")
        assert cred is not None
        assert cred.access_token_encrypted != "mock-access-token-12345"
        cipher = _fernet()
        decrypted_access = cipher.decrypt(cred.access_token_encrypted.encode()).decode()
        decrypted_refresh = cipher.decrypt(cred.refresh_token_encrypted.encode()).decode()
        assert decrypted_access == "mock-access-token-12345"
        assert decrypted_refresh == "mock-refresh-token-67890"

    status = connection_status()
    assert status["authorized"] is True
    assert status["expires_at"] is not None
