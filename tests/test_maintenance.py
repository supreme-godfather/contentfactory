import json

import pytest


def test_clear_pending_queues_preserves_published_jobs_and_audio(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.db import ClipCandidate, ClipJob, ContentJob, SessionLocal
    from app.maintenance import clear_pending_queues
    from app.services import create_job

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    pending = create_job("Pending cleanup", "youtube", request_id="pending")
    published = create_job("Published keep", "youtube", request_id="published")
    pending_media = tmp_path / f"{pending.id}.mp4"
    pending_media.write_bytes(b"pending")
    audio = tmp_path / "audio-bgm-shared.mp3"
    audio.write_bytes(b"shared")
    clip_media = tmp_path / "clip.mp4"
    youtube = tmp_path / "clip-youtube.jpg"
    instagram = tmp_path / "clip-instagram.jpg"
    for path in (clip_media, youtube, instagram):
        path.write_bytes(b"clip")
    with SessionLocal() as session:
        stored_pending = session.get(ContentJob, pending.id)
        stored_pending.render_path = str(pending_media)
        stored_pending.metadata_json = json.dumps({"assets": {"bgm": audio.name}})
        stored_published = session.get(ContentJob, published.id)
        stored_published.status = "published"
        clip_job = ClipJob(
            source_url_encrypted="encrypted",
            source_display="https://example.test/video",
            source_kind="vod",
            idempotency_key="clip-cleanup",
        )
        session.add(clip_job)
        session.flush()
        session.add(ClipCandidate(
            job_id=clip_job.id,
            rank=1,
            start_ms=0,
            end_ms=20000,
            score=500,
            title="clip",
            render_filename=clip_media.name,
            youtube_thumbnail_filename=youtube.name,
            instagram_thumbnail_filename=instagram.name,
            sha256="a" * 64,
            size_bytes=2048,
        ))
        session.commit()

    with pytest.raises(ValueError, match="confirmation"):
        clear_pending_queues("wrong")
    result = clear_pending_queues("CLEAR_PENDING_QUEUES")
    assert result["content_jobs"] == 1
    assert result["clip_jobs"] == 1
    assert not pending_media.exists()
    assert not clip_media.exists()
    assert audio.exists()
    with SessionLocal() as session:
        assert session.get(ContentJob, pending.id) is None
        assert session.get(ContentJob, published.id) is not None
