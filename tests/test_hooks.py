import json
from uuid import uuid4

from app.db import ContentJob, SessionLocal, init_db


def _script_job(status: str, script: str = "पहला वाक्य\n\nदूसरा वाक्य") -> str:
    init_db()
    with SessionLocal() as session:
        job = ContentJob(
            topic="Hook topic",
            platform="instagram",
            status=status,
            script_text=script,
            content_version=1,
            idempotency_key=f"hook-test-{uuid4()}",
            metadata_json=json.dumps(
                {"hook_variants": ["पहला विकल्प", "दूसरा विकल्प", "तीसरा विकल्प"]}
            ),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


def test_apply_hook_variant_replaces_the_opening():
    from app.hooks import apply_hook_variant

    job_id = _script_job("awaiting_script_approval")

    job = apply_hook_variant(job_id, 1)

    assert job.script_text.startswith("दूसरा विकल्प")
    assert "दूसरा वाक्य" in job.script_text
    assert job.content_version == 2
    assert job.approval_version is None
    assert job.status == "awaiting_script_approval"

    with SessionLocal() as session:
        stored = session.get(ContentJob, job_id)
        metadata = json.loads(stored.metadata_json)
    assert metadata["selected_hook"] == "दूसरा विकल्प"


def test_apply_hook_variant_rejects_unknown_index():
    from app.hooks import apply_hook_variant

    job_id = _script_job("awaiting_script_approval")

    try:
        apply_hook_variant(job_id, 7)
    except ValueError as exc:
        assert "out of range" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected a ValueError")


def test_hook_variants_helper_limits_results():
    from app.hooks import hook_variants

    variants = hook_variants({"hook_variants": ["a", "b", "c", "d"]})

    assert variants == ["a", "b", "c"]


def test_hook_variants_helper_ignores_bad_metadata():
    from app.hooks import hook_variants

    assert hook_variants({}) == []
    assert hook_variants({"hook_variants": "not-a-list"}) == []
