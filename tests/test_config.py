from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_owner_allowlist_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("TELEGRAM_OWNER_IDS", raising=False)

    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.telegram_owner_ids == ""
    assert settings.owner_ids == set()


def test_empty_owner_allowlist_rejects_everyone(monkeypatch):
    from app import security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(owner_ids=set()),
    )
    with pytest.raises(HTTPException) as error:
        security.require_owner(12345)

    assert error.value.status_code == 403
