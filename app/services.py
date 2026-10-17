from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import select

from .config import get_settings
from .db import ContentJob, FactorySetting, SessionLocal, WorkerLease


def create_job(topic: str, platform: str, brand: str = "learn_with_elite_explainer") -> ContentJob:
    clean_topic = " ".join(topic.split()).strip()
    if not clean_topic or len(clean_topic) > 240:
        raise ValueError("Topic must contain 1-240 non-whitespace characters")
    if platform not in {"youtube", "instagram", "both"}:
        raise ValueError("Platform must be youtube, instagram, or both")

    key = sha256(f"{brand}:{platform}:{clean_topic.lower()}".encode()).hexdigest()
    with SessionLocal() as session:
        existing = session.scalar(select(ContentJob).where(ContentJob.idempotency_key == key))
        if existing:
            return existing
        job = ContentJob(topic=clean_topic, platform=platform, brand=brand, idempotency_key=key)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def list_jobs(limit: int = 25) -> list[ContentJob]:
    with SessionLocal() as session:
        return list(session.scalars(select(ContentJob).order_by(ContentJob.created_at.desc()).limit(limit)))


def approve_job(job_id: str, approver_id: int) -> ContentJob:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status not in {"queued", "awaiting_approval"}:
            raise ValueError("Only queued or awaiting-approval jobs can be approved")
        job.status = "approved"
        job.approval_version = job.content_version
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def publish_job(job_id: str) -> ContentJob:
    settings = get_settings()
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if is_paused():
            raise ValueError("Factory is paused")
        if job.status != "approved" or job.approval_version != job.content_version:
            raise ValueError("Job requires approval for its current content version")
        if not settings.mock_publishing:
            raise ValueError("Real publishers are not enabled in this stage")
        job.status = "published"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def set_paused(paused: bool) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "paused")
        if setting is None:
            setting = FactorySetting(key="paused", value=str(paused).lower())
            session.add(setting)
        else:
            setting.value = str(paused).lower()
        session.commit()


def is_paused() -> bool:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "paused")
        if setting is not None:
            return setting.value == "true"
    return get_settings().app_paused


def worker_heartbeat(worker_id: str) -> None:
    with SessionLocal() as session:
        lease = session.get(WorkerLease, worker_id)
        now = datetime.now(timezone.utc)
        if lease is None:
            lease = WorkerLease(worker_id=worker_id, status="online", last_seen_at=now)
            session.add(lease)
        else:
            lease.status = "online"
            lease.last_seen_at = now
        session.commit()


def claim_render_job() -> ContentJob | None:
    with SessionLocal() as session:
        job = session.scalar(
            select(ContentJob)
            .where(ContentJob.status == "queued")
            .order_by(ContentJob.created_at.asc())
            .limit(1)
        )
        if job is None:
            return None
        job.status = "rendering"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job
