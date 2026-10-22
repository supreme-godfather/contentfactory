import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import or_, select

from .config import get_settings
from .db import ContentJob, FactorySetting, PublicationRecord, SessionLocal, WorkerLease
from .google_media import create_job_assets
from .publishers import get_publisher
from .quality import validate_content
from .scriptwriter import build_script
from .storyboard import (
    STYLE_ANCHOR,
    build_storyboard,
    caption_cues,
    estimate_duration_seconds,
)


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
        from .hq import get_format, get_format_key, get_niche_label

        format_key = get_format_key()
        format_info = get_format()
        niche_label = get_niche_label()
        job.script_text = build_script(job.topic, job.brand, format=format_key, niche=niche_label)
        storyboard = build_storyboard(job.topic, job.script_text)
        cues = caption_cues(job.script_text)
        estimated_seconds = estimate_duration_seconds(job.script_text)
        metadata = {
                "title": f"{job.topic} की असली कहानी | आसान हिंदी में",
                "description": (
                    f"{job.topic} को आसान हिंदी में समझिए।\n\n"
                    "💬 आपकी राय क्या है? Comment में ज़रूर बताइए।\n\n"
                    "YouTube: @LEARNWITHELITEEXPLAINER\n"
                    "Instagram: @sheoran_studios\n\n"
                    "#Hindi #Explainer #LearnWithEliteExplainer"
                ),
                "hashtags": ["#Hindi", "#Explainer", "#LearnWithEliteExplainer"],
                "tags": [
                    job.topic,
                    f"{job.topic} hindi",
                    "hindi explainer",
                    "learn with elite explainer",
                ],
                "disclosure": "AI-assisted production; facts require source verification.",
                "format": format_info["format_code"],
                "aspect_ratio": format_info["aspect_ratio"],
                "niche": niche_label,
                "duration_seconds": estimated_seconds,
                "target_duration_seconds": format_info["target_duration_seconds"],
                "story_arc": [scene["stage"] for scene in storyboard],
                "storyboard": storyboard,
                "style_anchor": STYLE_ANCHOR,
                "visual_direction": "original Hindi explainer visuals; no creator copying",
                "caption_policy": "burned captions required for muted playback",
                "caption_cues": cues,
                "approval_required": True,
            }
        assets = create_job_assets(job.id, job.topic, job.script_text, Path(get_settings().media_root))
        metadata["assets"] = assets
        job.metadata_json = json.dumps(
            metadata,
            ensure_ascii=False,
        )
        # The RDP must render the media before the operator can approve it.
        job.stage = "render"
        job.status = "queued"
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
        if job.status != "awaiting_approval":
            raise ValueError("Job must finish rendering before approval")
        job.status = "approved"
        job.approval_version = job.content_version
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def retry_job(job_id: str) -> ContentJob:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status != "failed":
            raise ValueError("Only failed jobs can be retried")
        job.status = "queued"
        job.stage = "render" if (job.script_text and job.metadata_json) else "topic"
        job.error_message = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.retry_count = (job.retry_count or 0) + 1
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def requeue_failed_jobs(max_retries: int) -> int:
    with SessionLocal() as session:
        failed_ids = list(
            session.scalars(
                select(ContentJob.id).where(
                    ContentJob.status == "failed",
                    ContentJob.retry_count < max_retries,
                )
            )
        )
    count = 0
    for job_id in failed_ids:
        try:
            retry_job(job_id)
            count += 1
        except ValueError:
            continue
    return count


def publish_job(job_id: str) -> ContentJob:
    settings = get_settings()
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if is_paused():
            raise ValueError("Factory is paused")
        # Enforce the configured daily publication cap (UTC day).
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        from sqlalchemy import func

        published_count = session.scalar(
            select(func.count(PublicationRecord.id)).where(
                PublicationRecord.status == "published",
                PublicationRecord.created_at >= day_start,
            )
        ) or 0
        from .hq import get_daily_publish_limit

        if published_count >= get_daily_publish_limit():
            raise ValueError("Daily publication limit reached")
        if job.status != "approved" or job.approval_version != job.content_version:
            raise ValueError("Job requires approval for its current content version")
        report = validate_content(
            topic=job.topic,
            script=job.script_text,
            metadata_json=job.metadata_json,
            render_path=job.render_path,
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
                media_path=job.render_path,
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
        # Enforce the configured concurrency cap: count live rendering leases.
        from sqlalchemy import func

        active_rendering = session.scalar(
            select(func.count(ContentJob.id)).where(
                ContentJob.status == "rendering",
                ContentJob.lease_expires_at.is_not(None),
                ContentJob.lease_expires_at > now,
            )
        ) or 0
        if active_rendering >= max(1, get_settings().max_concurrent_jobs):
            return None
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
