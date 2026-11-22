import json
from datetime import datetime, timezone
from uuid import uuid4

from app.db import ContentJob, PublicationRecord, SessionLocal, init_db


def _job(topic: str, **overrides):
    init_db()
    with SessionLocal() as session:
        job = ContentJob(
            topic=topic,
            platform="instagram",
            status="approved",
            idempotency_key=f"preflight-{uuid4()}",
            metadata_json=json.dumps({"thumbnails": {"instagram": "cover.jpg"}}),
        )
        for key, value in overrides.items():
            setattr(job, key, value)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def test_preflight_reports_missing_render(tmp_path):
    from app.preflight import run_preflight

    job = _job("Preview topic", render_path=str(tmp_path / "missing.mp4"))

    problems = run_preflight(
        job,
        {},
        ["instagram"],
        mock_publishing=False,
        settings=type("S", (), {"public_base_url": "https://example.com"})(),
    )

    assert "render_file_missing" in problems


def test_preflight_accepts_a_ready_render(tmp_path):
    from app.preflight import run_preflight

    media = tmp_path / "render.mp4"
    media.write_bytes(b"v" * 4096)
    job = _job("Ready topic", render_path=str(media))

    problems = run_preflight(
        job,
        {"thumbnails": {"instagram": "cover.jpg"}},
        ["instagram"],
        mock_publishing=False,
        settings=type(
            "S",
            (),
            {"public_base_url": "https://example.com", "instagram_enabled": False},
        )(),
    )

    assert problems == []


def test_preflight_blocks_recent_duplicate_topic(tmp_path):
    from app.preflight import run_preflight

    media = tmp_path / "render.mp4"
    media.write_bytes(b"v" * 4096)
    first = _job("Duplicate topic", render_path=str(media))
    with SessionLocal() as session:
        session.add(
            PublicationRecord(
                job_id=first.id,
                platform="instagram",
                idempotency_key=f"{first.id}:1:instagram",
                status="published",
                remote_id="remote-1",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    second = _job("Duplicate topic", render_path=str(media))

    problems = run_preflight(
        second,
        {"thumbnails": {"instagram": "cover.jpg"}},
        ["instagram"],
        mock_publishing=False,
        settings=type("S", (), {"public_base_url": "https://example.com"})(),
    )

    assert "duplicate_topic_published_recently" in problems


def test_qc_problems_require_measurement_when_enforced():
    from app.preflight import qc_problems

    assert qc_problems({}, required=False) == []
    assert qc_problems({}, required=True) == ["media_qc:media_not_measured"]


def test_qc_problems_report_measured_blockers():
    from app.preflight import qc_problems

    metadata = {
        "media_qc": {
            "measured": True,
            "probe_ok": True,
            "width": 1080,
            "height": 1920,
            "fps": 30.0,
            "duration_seconds": 40.0,
            "has_audio": False,
            "bytes": 4_000_000,
        }
    }

    assert qc_problems(metadata, required=False) == ["media_qc:no_audio_stream"]
