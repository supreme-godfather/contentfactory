from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_webhook_rejects_missing_or_invalid_secret(monkeypatch):
    from app import security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(telegram_webhook_secret="invalid secret"),
    )
    with pytest.raises(HTTPException) as error:
        security.verify_webhook_secret("invalid secret")
    assert error.value.status_code == 503


def test_webhook_requires_and_compares_configured_secret(monkeypatch):
    from app import security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(telegram_webhook_secret="valid_secret-123"),
    )
    with pytest.raises(HTTPException) as error:
        security.verify_webhook_secret(None)
    assert error.value.status_code == 401

    with pytest.raises(HTTPException) as error:
        security.verify_webhook_secret("wrong_secret")
    assert error.value.status_code == 401

    assert security.verify_webhook_secret("valid_secret-123") is None


def test_webhook_rejects_unconfigured_secret(monkeypatch):
    from app import security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(telegram_webhook_secret=""),
    )
    with pytest.raises(HTTPException) as error:
        security.verify_webhook_secret(None)
    assert error.value.status_code == 503
