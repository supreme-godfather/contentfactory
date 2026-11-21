import os
import shutil
import tempfile
from pathlib import Path

import pytest


_TEST_DIR = Path(tempfile.mkdtemp(prefix="factory-pytest-"))
_TEST_DB = _TEST_DIR / "factory.db"
_ORIGINAL_DATABASE_URL = os.environ.get("DATABASE_URL")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"


@pytest.fixture(scope="session", autouse=True)
def _test_database_lifetime():
    yield

    from app.db import engine

    engine.dispose()
    if _ORIGINAL_DATABASE_URL is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _ORIGINAL_DATABASE_URL
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_test_database(_test_database_lifetime):
    from app.db import Base, engine, init_db

    assert Path(engine.url.database).resolve() == _TEST_DB.resolve()
    init_db()

    def clear() -> None:
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())
        try:
            from app.telegram_icons import invalidate_icon_cache

            invalidate_icon_cache()
        except ImportError:
            pass

    clear()
    yield
    clear()
