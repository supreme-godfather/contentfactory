import pytest


def test_cost_estimate_covers_video_images_and_speech():
    from app.cost import estimate_job_cost

    estimate = estimate_job_cost(
        {"video_provider": "minimax", "image_provider": "minimax", "voice_provider": "minimax"},
        "अ" * 1000,
    )
    assert estimate["minimax_video"] > 0
    assert estimate["minimax_image"] > 0
    assert estimate["minimax_speech"] > 0
    assert estimate["total"] == pytest.approx(
        estimate["minimax_video"]
        + estimate["minimax_image"]
        + estimate["minimax_speech"]
        + estimate["script_llm"],
        rel=1e-6,
    )


def test_cost_ceiling_blocks_an_expensive_job(tmp_path, monkeypatch):
    from app.cost import enforce_cost_ceiling, record_usage
    from app.hq import set_auto_publish_max_cost_usd

    set_auto_publish_max_cost_usd(0.2)
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="Cost ceiling"):
        enforce_cost_ceiling(
            "job-1",
            {"video_provider": "minimax", "image_provider": "minimax", "voice_provider": "minimax"},
            "अ" * 1000,
        )
    set_auto_publish_max_cost_usd(1.5)


def test_usage_is_written_to_the_ledger_and_summed(tmp_path, monkeypatch):
    from app.cost import job_cost, record_usage, spend_summary

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    record_usage("job-9", "minimax", "video", 5, "second", 0.06)
    record_usage("job-9", "minimax", "image", 1, "image", 0.022)

    assert job_cost("job-9") == pytest.approx(0.322, rel=1e-3)
    summary = spend_summary(30)
    assert summary["total_usd"] >= 0.322
    assert "minimax" in summary["by_provider"]


def test_backlog_cleanup_only_removes_script_review_jobs(tmp_path, monkeypatch):
    from app.db import ContentJob, SessionLocal
    from app.maintenance import clear_script_review_backlog
    from app.services import create_job

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    review = create_job("पुराना काम", "youtube", request_id="backlog-review")
    keep = create_job("चल रहा है", "youtube", request_id="backlog-keep")
    with SessionLocal() as session:
        stored = session.get(ContentJob, review.id)
        stored.status = "awaiting_script_approval"
        stored.stage = "script"
        keep_job = session.get(ContentJob, keep.id)
        keep_job.status = "queued"
        keep_job.stage = "render"
        session.commit()

    result = clear_script_review_backlog()
    assert result["script_review_jobs_removed"] == 1
    with SessionLocal() as session:
        assert session.get(ContentJob, review.id) is None
        assert session.get(ContentJob, keep.id) is not None
