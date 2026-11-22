import json

import pytest
from fastapi import HTTPException


def _rendering_job(worker_id: str = "worker-1"):
    from app.db import ContentJob, SessionLocal, init_db
    from app.services import create_job

    init_db()
    job = create_job("QC topic", "instagram")
    with SessionLocal() as session:
        stored = session.get(ContentJob, job.id)
        stored.status = "rendering"
        stored.stage = "render"
        stored.claimed_by = worker_id
        session.commit()
    return job.id


GOOD_REPORT = {
    "measured": True,
    "probe_ok": True,
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "duration_seconds": 47.5,
    "has_audio": True,
    "bytes": 4_000_000,
    "integrated_lufs": -13.9,
    "true_peak_dbfs": -1.4,
}

BAD_REPORT = dict(GOOD_REPORT, has_audio=False)


def test_worker_qc_stores_the_report_and_reports_ok():
    from app import main
    from app.db import ContentJob, SessionLocal

    job_id = _rendering_job()

    result = main.worker_media_qc(job_id, "worker-1", GOOD_REPORT, None)

    assert result["ok"] is True
    assert result["blockers"] == []
    with SessionLocal() as session:
        stored = session.get(ContentJob, job_id)
        metadata = json.loads(stored.metadata_json)
    assert metadata["media_qc"]["width"] == 1080
    assert "media_qc_at" in metadata


def test_worker_qc_reports_blockers():
    from app import main

    job_id = _rendering_job()

    result = main.worker_media_qc(job_id, "worker-1", BAD_REPORT, None)

    assert result["ok"] is False
    assert "no_audio_stream" in result["blockers"]


def test_worker_qc_rejects_a_job_owned_by_another_worker():
    from app import main

    job_id = _rendering_job("worker-1")

    with pytest.raises(HTTPException) as error:
        main.worker_media_qc(job_id, "worker-2", GOOD_REPORT, None)

    assert error.value.status_code == 409


def test_complete_requires_a_measured_render():
    from app import main

    job_id = _rendering_job()

    with pytest.raises(HTTPException) as error:
        main.worker_complete(job_id, "worker-1", "media/render.mp4", None)

    assert error.value.status_code == 409
    assert "measured" in str(error.value.detail)


def test_complete_accepts_a_measured_render():
    from app import main

    job_id = _rendering_job()
    main.worker_media_qc(job_id, "worker-1", GOOD_REPORT, None)

    result = main.worker_complete(job_id, "worker-1", "media/render.mp4", None)

    assert result["status"] == "awaiting_approval"


def test_complete_rejects_a_failed_measurement():
    from app import main

    job_id = _rendering_job()
    main.worker_media_qc(job_id, "worker-1", BAD_REPORT, None)

    with pytest.raises(HTTPException) as error:
        main.worker_complete(job_id, "worker-1", "media/render.mp4", None)

    assert error.value.status_code == 409
    assert "no_audio_stream" in str(error.value.detail)


def test_health_endpoint_returns_a_snapshot():
    from app import main

    result = main.pipeline_health_endpoint(None)

    assert result["status"] in {"healthy", "degraded", "paused"}
    assert "providers" in result
