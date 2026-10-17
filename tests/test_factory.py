from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_import_is_clean():
    from app.main import app

    assert app.title == "Hindi Content Factory"


def test_factory_starts_paused():
    from app.config import get_settings

    assert get_settings().app_paused is True
