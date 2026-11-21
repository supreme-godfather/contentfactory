from cryptography.fernet import Fernet
import pytest


@pytest.fixture(autouse=True)
def clipping_encryption(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/video",
        "https://localhost/video",
        "https://127.0.0.1/video",
        "https://user:secret@example.com/video",
        "https://example.com/video#fragment",
        "https://internal/video",
    ],
)
def test_clip_source_rejects_unsafe_urls(url):
    from app.clipping import validate_clip_source_url

    with pytest.raises(ValueError):
        validate_clip_source_url(url)


def test_clip_job_claim_candidates_and_completion():
    from app.clipping import (
        claim_clip_job,
        complete_clip_job,
        create_clip_job,
        list_clip_candidates,
        register_clip_candidate,
    )

    job = create_clip_job(
        "https://video.example/watch?v=123",
        "vod",
        request_id="telegram:1",
    )
    claimed = claim_clip_job("railway-clipper")
    assert claimed is not None
    claimed_job, lease_token, source_url = claimed
    assert claimed_job.id == job.id
    assert source_url == "https://video.example/watch?v=123"
    assert "?" not in claimed_job.source_display

    ranges = [(0, 20000), (25000, 50000), (55000, 85000)]
    for rank, (start, end) in enumerate(ranges, start=1):
        candidate = register_clip_candidate(
            job.id,
            "railway-clipper",
            lease_token,
            rank=rank,
            start_ms=start,
            end_ms=end,
            score=900 - rank,
            title=f"Candidate {rank}",
            reason="complete_sentence",
                transcript_excerpt="A useful complete moment.",
                filename=f"{job.id}-clip-{rank}.mp4",
                youtube_thumbnail_filename=f"{job.id}-clip-{rank}-youtube.jpg",
                instagram_thumbnail_filename=f"{job.id}-clip-{rank}-instagram.jpg",
            checksum=(str(rank) * 64),
            size_bytes=2048,
        )
        assert candidate.rank == rank

    completed = complete_clip_job(
        job.id,
        "railway-clipper",
        lease_token,
        {
            "duration_ms": 120000,
            "extractor": "test",
            "actual_kind": "vod",
            "caption_source": "vtt",
        },
    )
    assert completed.status == "awaiting_review"
    assert [candidate.rank for candidate in list_clip_candidates(job.id)] == [1, 2, 3]


def test_clip_candidates_must_not_overlap():
    from app.clipping import claim_clip_job, create_clip_job, register_clip_candidate

    job = create_clip_job("https://video.example/source", "vod", request_id="overlap")
    _, lease_token, _ = claim_clip_job("railway-clipper")
    common = {
        "job_id": job.id,
        "worker_id": "railway-clipper",
        "lease_token": lease_token,
        "score": 800,
        "reason": "test",
        "transcript_excerpt": "test",
        "size_bytes": 2048,
        "youtube_thumbnail_filename": f"{job.id}-clip-youtube.jpg",
        "instagram_thumbnail_filename": f"{job.id}-clip-instagram.jpg",
    }
    register_clip_candidate(
        rank=1,
        start_ms=10000,
        end_ms=40000,
        title="First",
        filename=f"{job.id}-clip-1.mp4",
        checksum="a" * 64,
        **common,
    )
    with pytest.raises(ValueError, match="must not overlap"):
        register_clip_candidate(
            rank=2,
            start_ms=35000,
            end_ms=55000,
            title="Overlap",
            filename=f"{job.id}-clip-2.mp4",
            checksum="b" * 64,
            **common,
        )


def test_only_dedicated_clip_worker_can_claim():
    from app.clipping import claim_clip_job, create_clip_job

    create_clip_job("https://video.example/source", "vod", request_id="worker")
    assert claim_clip_job("railway-renderer") is None
    assert claim_clip_job("railway-clipper") is not None


def test_failed_clip_job_can_be_retried():
    from app.clipping import claim_clip_job, create_clip_job, fail_clip_job, retry_clip_job

    job = create_clip_job("https://video.example/retry", "vod", request_id="retry")
    _, lease_token, _ = claim_clip_job("railway-clipper")
    failed = fail_clip_job(job.id, "railway-clipper", lease_token, "source_download_failed")
    assert failed.status == "failed"
    retried = retry_clip_job(job.id)
    assert retried.status == "queued"
    assert retried.stage == "ingest"
    assert retried.error_message is None


def test_approved_clip_can_be_promoted_to_publish_queue(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.clipping import (
        claim_clip_job,
        complete_clip_job,
        create_clip_job,
        list_clip_candidates,
        promote_clip_candidate,
        register_clip_candidate,
        review_clip_candidate,
    )

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    job = create_clip_job("https://video.example/promote", "vod", request_id="promote")
    _, lease_token, _ = claim_clip_job("railway-clipper")
    for rank, start in enumerate((0, 25000, 55000), start=1):
        end = start + 20000
        video = f"{job.id}-clip-{rank}.mp4"
        youtube = f"{job.id}-clip-{rank}-youtube.jpg"
        instagram = f"{job.id}-clip-{rank}-instagram.jpg"
        (tmp_path / video).write_bytes(b"video")
        (tmp_path / youtube).write_bytes(b"youtube")
        (tmp_path / instagram).write_bytes(b"instagram")
        register_clip_candidate(
            job.id,
            "railway-clipper",
            lease_token,
            rank=rank,
            start_ms=start,
            end_ms=end,
            score=900 - rank,
            title=f"Promoted clip {rank}",
            reason="test",
            transcript_excerpt="This is a complete transcript excerpt for publishing metadata.",
            filename=video,
            youtube_thumbnail_filename=youtube,
            instagram_thumbnail_filename=instagram,
            checksum=(str(rank) * 64),
            size_bytes=2048,
        )
    complete_clip_job(job.id, "railway-clipper", lease_token, {"duration_ms": 90000})
    candidate = list_clip_candidates(job.id)[0]
    review_clip_candidate(candidate.id, "approved", 12345)
    promoted = promote_clip_candidate(candidate.id, "both", 12345)

    assert promoted.status == "approved"
    assert promoted.approval_version == promoted.content_version
    assert promoted.render_path == str((tmp_path / candidate.render_filename).resolve())
