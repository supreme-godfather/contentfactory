import json
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import or_, select

from .config import get_settings
from .db import ContentJob, FactorySetting, PublicationRecord, SessionLocal, WorkerLease
from .publishers import get_publisher
from .quality import validate_content


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


def generate_content(job_id: str) -> ContentJob:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status not in {"queued", "rendering"}:
            raise ValueError("Job is not available for content generation")
        job.script_text = (
            f"क्या आपने कभी सोचा है कि {job.topic} के पीछे असली कहानी क्या है?\n\n"
            f"इस वीडियो में हम {job.topic} को आसान हिंदी में समझेंगे। "
            "मुख्य तथ्यों, प्रभाव और सीख को संक्षेप में देखिए।\n\n"
            "ऐसी जानकारी के लिए YouTube पर Learn With Elite Explainer को subscribe "
            "और Instagram पर sheoran_studios को follow करें।"
        )
        job.metadata_json = json.dumps(
            {
                "title": f"{job.topic} की असली कहानी | आसान हिंदी में",
                "description": (
                    f"{job.topic} को आसान हिंदी में समझिए।\n\n"
                    "YouTube: @LEARNWITHELITEEXPLAINER\n"
                    "Instagram: @sheoran_studios"
                ),
                "hashtags": ["#Hindi", "#Explainer", "#LearnWithEliteExplainer"],
                "disclosure": "AI-assisted production; facts require source verification.",
                "format": "vertical_short",
                "duration_seconds": 60,
                "story_arc": ["hook", "problem", "stakes", "solution", "payoff"],
                "visual_direction": "original Hindi explainer visuals; no creator copying",
                "caption_policy": "burned captions required for muted playback",
                "approval_required": True,
            },
            ensure_ascii=False,
        )
        job.stage = "quality_check"
        job.status = "awaiting_approval"
        job.updated_at = datetime.now(timezone.utc)
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
        report = validate_content(
            topic=job.topic,
            script=job.script_text,
            metadata_json=job.metadata_json,
            render_path=job.render_path or f"mock://{job.id}",
        )
        if not report.passed:
            raise ValueError(f"Quality gate blocked publication: {', '.join(report.blockers)}")
        platforms = ["youtube", "instagram"] if job.platform == "both" else [job.platform]
        metadata = json.loads(job.metadata_json or "{}")
        for platform in platforms:
            key = f"{job.id}:{job.content_version}:{platform}"
            record = session.scalar(select(PublicationRecord).where(PublicationRecord.idempotency_key == key))
            if record and record.status == "published":
                continue
            result = get_publisher(platform).publish(
                title=metadata["title"],
                description=metadata["description"],
                media_path=job.render_path or f"mock://{job.id}",
            )
            if record is None:
                record = PublicationRecord(job_id=job.id, platform=platform, idempotency_key=key)
                session.add(record)
            record.status = result.status
            record.remote_id = result.remote_id
            record.remote_url = result.remote_url
            record.error_message = result.detail if result.status != "published" else None
            if result.status != "published":
                raise ValueError(result.detail)
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


def claim_render_job(worker_id: str) -> ContentJob | None:
    now = datetime.now(timezone.utc)
    lease_until = now.timestamp() + get_settings().worker_lease_seconds
    lease_expires = datetime.fromtimestamp(lease_until, timezone.utc)
    with SessionLocal() as session:
        job = session.scalar(
            select(ContentJob)
            .where(
                or_(
                    ContentJob.status == "queued",
                    (ContentJob.status == "rendering")
                    & (
                        (ContentJob.lease_expires_at.is_(None))
                        | (ContentJob.lease_expires_at < now)
                    ),
                )
            )
            .order_by(ContentJob.created_at.asc())
            .limit(1)
        )
        if job is None:
            return None
        job.status = "rendering"
        job.claimed_by = worker_id
        job.lease_expires_at = lease_expires
        job.updated_at = now
        session.commit()
        session.refresh(job)
        return job


def complete_render_job(job_id: str, worker_id: str, render_path: str) -> ContentJob:
    if not render_path or len(render_path) > 500:
        raise ValueError("Invalid render path")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering" or job.claimed_by != worker_id:
            raise ValueError("Job is not currently rendering")
        job.render_path = render_path
        job.stage = "quality_check"
        job.status = "awaiting_approval"
        job.claimed_by = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def fail_render_job(job_id: str, worker_id: str, error_message: str) -> ContentJob:
    if not error_message or len(error_message) > 1000:
        raise ValueError("Invalid render error")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering" or job.claimed_by != worker_id:
            raise ValueError("Job is not currently rendering")
        job.status = "failed"
        job.stage = "render"
        job.error_message = error_message
        job.claimed_by = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job
