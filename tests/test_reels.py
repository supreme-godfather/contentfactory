import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'reels.db'}")
    monkeypatch.setenv("APP_PAUSED", "true")
    monkeypatch.setenv("MOCK_PUBLISHING", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_reel_profile_is_the_default_format():
    from app.hq import get_format, get_format_key

    assert get_format_key() == "reel"
    info = get_format()
    assert info["format_code"] == "instagram_reel"
    assert info["aspect_ratio"] == "9:16"
    assert 45 <= info["target_duration_seconds"] <= 60


def test_generated_instagram_job_contains_reel_metadata():
    from app.db import Base, engine, init_db
    from app.services import create_job, generate_content

    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    job = generate_content(create_job("मन को नियंत्रित करने का सच", "instagram").id)
    metadata = json.loads(job.metadata_json)
    assert metadata["profile"] == "quiet_wolf_hindi"
    assert metadata["format"] == "instagram_reel"
    assert metadata["aspect_ratio"] == "9:16"
    assert metadata["approval_required"] is True
    assert "#Mindset" in metadata["hashtags"]


def test_storyboard_uses_dark_cinematic_anchor():
    from app.storyboard import STYLE_ANCHOR

    assert "dark-cinematic" in STYLE_ANCHOR
    assert "faceless" in STYLE_ANCHOR


def test_scheduled_reminder_requires_approved_current_version():
    from app.db import Base, ContentJob, SessionLocal, engine, init_db
    from app.services import approve_job, create_job, generate_content, schedule_job

    init_db()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    job = generate_content(create_job("మనసు ఎందుకు కథలను నమ్ముతుంది", "instagram").id)
    with SessionLocal() as session:
        stored = session.get(ContentJob, job.id)
        stored.status = "awaiting_approval"
        stored.render_path = "data/media/sample.mp4"
        session.commit()
    with pytest.raises(ValueError, match="Approve"):
        schedule_job(job.id, datetime.now(timezone.utc) + timedelta(hours=1))
    approved = approve_job(job.id, 123)
    scheduled = schedule_job(
        job.id,
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert scheduled.approval_version == approved.content_version
    assert scheduled.scheduled_at is not None
