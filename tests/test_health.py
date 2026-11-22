from app.db import init_db


def test_pipeline_health_reports_a_status():
    from app.health import pipeline_health

    init_db()

    health = pipeline_health()

    assert health["status"] in {"healthy", "degraded", "paused"}
    assert "workers" in health
    assert "queues" in health
    assert "publications" in health
    assert "providers" in health
    assert "media_qc" in health
    assert isinstance(health["issues"], list)


def test_pipeline_health_counts_queued_jobs():
    from uuid import uuid4

    from app.db import ContentJob, SessionLocal
    from app.health import pipeline_health

    init_db()
    with SessionLocal() as session:
        session.add(
            ContentJob(
                topic="Queued topic",
                platform="instagram",
                status="queued",
                idempotency_key=f"health-{uuid4()}",
            )
        )
        session.commit()

    health = pipeline_health()

    assert health["queues"].get("queued") == 1
    assert health["active_jobs"] >= 1


def test_pipeline_health_surfaces_missing_credentials():
    from app.health import pipeline_health

    init_db()

    health = pipeline_health()

    providers = health["providers"]
    assert "youtube" in providers
    assert "instagram" in providers
    assert providers["youtube"]["enabled"] in {True, False}
