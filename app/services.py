import json
import re
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


def _topic_hashtags(topic: str) -> list[str]:
    words = re.findall(r"[^\W_]+", topic, flags=re.UNICODE)
    tags = []
    for word in words:
        if len(word) < 2:
            continue
        tag = f"#{word[:28]}"
        if tag.casefold() not in {item.casefold() for item in tags}:
            tags.append(tag)
        if len(tags) == 2:
            break
    return tags


def _build_publish_metadata(
    *,
    topic: str,
    format_key: str,
    niche_label: str,
) -> dict[str, object]:
    topic = " ".join(topic.split())
    title = f"{topic} | आसान हिंदी में"[:100]
    topic_tags = _topic_hashtags(topic)
    niche_lower = niche_label.casefold()
    if "psychology" in niche_lower:
        niche_tags = ["#Psychology", "#Mindset"]
    elif "motivation" in niche_lower:
        niche_tags = ["#Motivation", "#LifeLessons"]
    elif "history" in niche_lower:
        niche_tags = ["#History", "#Facts"]
    elif "science" in niche_lower:
        niche_tags = ["#Science", "#Facts"]
    elif "money" in niche_lower or "business" in niche_lower:
        niche_tags = ["#Finance", "#Business"]
    else:
        niche_tags = ["#Facts", "#Education"]
    hashtags = list(dict.fromkeys([
        *topic_tags,
        *niche_tags,
        "#Hindi",
        "#Reels" if format_key == "reel" else "#YouTube",
    ]))
    description = (
        f"{topic} को आसान हिंदी में समझिए।\n\n"
        f"इस { 'Reel' if format_key == 'reel' else 'वीडियो' } में "
        f"{niche_label} से जुड़ी कहानी, ज़रूरी बातें और मुख्य सीख देखें।\n\n"
        "आपकी राय क्या है? नीचे comment में बताइए।\n\n"
        "AI-assisted production; महत्वपूर्ण तथ्यों को प्रकाशन से पहले जाँचें।"
    )
    tags = list(dict.fromkeys([
        topic,
        f"{topic} hindi",
        niche_label,
        "Hindi explainer",
        "Hindi educational video",
    ]))
    while sum(len(tag) + 1 for tag in tags) > 450:
        tags.pop()
    youtube_description = f"{description}\n\n{' '.join(hashtags[:3])}"
    instagram_caption = f"{title}\n\n{description}\n\n{' '.join(hashtags)}"
    return {
        "title": title,
        "description": youtube_description,
        "hashtags": hashtags,
        "tags": tags,
        "youtube": {
            "title": title,
            "description": youtube_description,
            "tags": tags,
        },
        "instagram": {
            "caption": instagram_caption[:2200],
            "hashtags": hashtags,
        },
    }


def create_job(
    topic: str,
    platform: str,
    brand: str = "learn_with_elite_explainer",
    request_id: str | None = None,
) -> ContentJob:
    clean_topic = " ".join(topic.split()).strip()
    if not clean_topic or len(clean_topic) > 240:
        raise ValueError("Topic must contain 1-240 non-whitespace characters")
    if platform not in {"youtube", "instagram", "both"}:
        raise ValueError("Platform must be youtube, instagram, or both")

    idempotency_scope = f"{brand}:{platform}:{clean_topic.lower()}"
    if request_id is not None:
        idempotency_scope = f"{idempotency_scope}:{request_id}"
    key = sha256(idempotency_scope.encode()).hexdigest()
    with SessionLocal() as session:
        existing = session.scalar(select(ContentJob).where(ContentJob.idempotency_key == key))
        if existing:
            return existing
        job = ContentJob(topic=clean_topic, platform=platform, brand=brand, idempotency_key=key)
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def plan_custom_series(brief: str, count: int) -> dict[str, object]:
    """Small deterministic planning assistant for custom series briefs.

    It extracts the practical production directions without making a paid
    model call, so the workflow stays usable and approval-gated.
    """
    text = " ".join(brief.split()).strip()
    lowered = text.casefold()
    count = max(2, min(count, 12))

    if any(word in lowered for word in ("हास्य", "funny", "comedy", "मज़ेदार", "मजेदार")):
        tone = "energetic, witty and family-safe"
    elif any(word in lowered for word in ("भयानक", "डरावना", "horror", "scary")):
        tone = "dark, suspenseful and cinematic"
    elif any(word in lowered for word in ("serious", "गंभीर", "news", "सत्य", "investigation")):
        tone = "serious, factual and cinematic"
    else:
        tone = "curious, emotional and motivational"

    if any(word in lowered for word in ("kids", "child", "बच्च", "easy", "simple", "आसान")):
        audience = "families and beginners"
    elif any(word in lowered for word in ("teen", "students", "student", "युवा", "college")):
        audience = "young viewers and students"
    else:
        audience = "general Hindi-speaking viewers"

    if any(word in lowered for word in ("cinematic", "film", "movie", "सिनेम", "डॉक्यूमेंट्री")):
        visual_style = "premium cinematic documentary"
    elif any(word in lowered for word in ("animation", "cartoon", "illustrated", "एनिम")):
        visual_style = "bold illustrated 9:16 animation"
    else:
        visual_style = "premium dark-cinematic faceless Reel"

    return {
        "mode": "deterministic_assistant",
        "tone": tone,
        "audience": audience,
        "visual_style": visual_style,
        "part_count": count,
        "hook_style": "surprising question in the first 3 seconds",
        "continuation_style": "strong unresolved clue or consequence at every ending",
        "retention_style": "one core reveal per part; escalating story tension",
        "caption_style": "bold 2-line Hindi captions with 2-4 second visual beats",
        "thumbnail_style": "high-contrast 9:16, one face or symbolic object, 2-4 Hindi words",
        "fact_policy": "use verifiable claims only; flag uncertain facts for owner review",
    }


def create_custom_jobs(
    brief: str,
    platform: str,
    *,
    parts: int = 6,
) -> list[ContentJob]:
    """Create an approval-gated owner-briefed series with 2-12 variable parts."""
    if platform not in {"youtube", "instagram", "both"}:
        raise ValueError("Platform must be youtube, instagram, or both")
    clean_brief = " ".join(brief.split()).strip()
    if not clean_brief or len(clean_brief) > 240:
        raise ValueError("Custom brief must contain 1-240 non-whitespace characters")
    count = max(2, min(parts, 12))
    ai_plan = plan_custom_series(clean_brief, count)
    jobs: list[ContentJob] = []
    for part in range(1, count + 1):
        topic = f"{clean_brief} — Part {part}/{count}"
        job = create_job(
            topic,
            platform,
            request_id=f"custom:telegram:{part}:{sha256(clean_brief.casefold().encode()).hexdigest()[:16]}",
        )
        if job.status == "queued" and job.stage == "topic":
            with SessionLocal() as session:
                stored = session.get(ContentJob, job.id)
                if part == count:
                    arc_stage = "final payoff and satisfying conclusion"
                elif part == 1:
                    arc_stage = "setup, mystery and first reveal"
                else:
                    progress = round((part - 1) / max(count - 1, 1), 2)
                    arc_stage = "build tension and reveal clue" if progress < 0.75 else "late escalation and reversal"
                stored.script_text = build_script(
                    topic,
                    format="reel",
                    custom_instruction=(
                        f"Create this as one connected 45-second Short in a {count}-part story. "
                        f"This is Part {part}/{count}. End with a strong continuation hook "
                        "and do not repeat the same opening, facts or CTA as other parts. "
                        f"Tone: {ai_plan['tone']}. Audience: {ai_plan['audience']}. "
                        f"Visual style: {ai_plan['visual_style']}. Story stage: {arc_stage}. "
                        "The final part must resolve the core story while preserving the discussion CTA."
                        f" Owner brief: {clean_brief}"
                    ),
                )
                storyboard = build_storyboard(topic, stored.script_text)
                cues = caption_cues(stored.script_text)
                estimated_seconds = estimate_duration_seconds(stored.script_text)
                metadata = {
                    **_build_publish_metadata(
                        topic=topic,
                        format_key="reel",
                        niche_label=get_settings().reel_profile_name,
                    ),
                    "custom_creator": True,
                    "series_brief": clean_brief,
                    "series_part": part,
                    "series_count": count,
                    "ai_plan": ai_plan,
                    "arc_stage": arc_stage,
                    "title_options": [
                        f"भाग {part}: {clean_brief}"[:100],
                        f"{clean_brief} — पार्ट {part}"[:100],
                        f"{count} भागों की कहानी — {part}वा भाग"[:100],
                    ],
                    "thumbnail_prompt": (
                        f"premium Hindi 9:16 vertical thumbnail for Part {part}/{count}, "
                        f"{ai_plan['visual_style']}, {arc_stage}, "
                        "high contrast red-orange accent, 2-4 word Hindi title, "
                        "cinematic close-up or symbolic object, no real-person likeness, no copied creator style"
                    ),
                    "disclosure": "AI-assisted production; facts require source verification.",
                    "format": "9:16 vertical Short",
                    "aspect_ratio": "9:16",
                    "storyboard": storyboard,
                    "caption_cues": cues,
                    "duration_seconds": estimated_seconds,
                    "approval_required": True,
                }
                stored.metadata_json = json.dumps(metadata, ensure_ascii=False)
                assets = create_job_assets(
                    stored.id,
                    topic,
                    stored.script_text,
                    Path(get_settings().media_root),
                    storyboard=storyboard,
                    thumbnail_prompt=str(metadata["thumbnail_prompt"]),
                )
                metadata["assets"] = assets
                stored.metadata_json = json.dumps(metadata, ensure_ascii=False)
                stored.stage = "render"
                stored.status = "queued"
                stored.updated_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(stored)
                job = stored
        jobs.append(job)
    return jobs


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
                **_build_publish_metadata(
                    topic=job.topic,
                    format_key=format_key,
                    niche_label=niche_label,
                ),
                "disclosure": "AI-assisted production; facts require source verification.",
                "format": format_info["format_code"],
                "aspect_ratio": format_info["aspect_ratio"],
                "niche": niche_label,
                "duration_seconds": estimated_seconds,
                "target_duration_seconds": format_info["target_duration_seconds"],
                "story_arc": [scene["stage"] for scene in storyboard],
                "storyboard": storyboard,
                "style_anchor": STYLE_ANCHOR,
                "visual_direction": (
                    "dark cinematic faceless Reel; high-contrast captions; original visuals; no creator copying"
                    if format_key == "reel"
                    else "original Hindi explainer visuals; no creator copying"
                ),
                "profile": (
                    get_settings().reel_profile_name
                    if format_key == "reel"
                    else "hindi_explainer"
                ),
                "caption_policy": "burned captions required for muted playback",
                "caption_cues": cues,
                "approval_required": True,
            }
        assets = create_job_assets(
            job.id,
            job.topic,
            job.script_text,
            Path(get_settings().media_root),
            storyboard=storyboard,
        )
        from .provider_credentials import get_provider_for_role, media_generation_enabled

        video_provider = get_provider_for_role("video")
        if (
            media_generation_enabled()
            and video_provider in {"minimax", "higgsfield"}
            and not any(key.startswith("scene_video_") for key in assets)
        ):
            raise RuntimeError(
                f"{video_provider} was selected, but no provider-generated video asset was created."
            )
        metadata["assets"] = assets
        metadata["video_provider"] = video_provider
        job.metadata_json = json.dumps(
            metadata,
            ensure_ascii=False,
        )
        # A connected render worker must render the media before approval.
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


def cancel_job(job_id: str) -> ContentJob:
    """Cancel work that has not been published or is actively rendering."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status in {"published", "rendering"}:
            raise ValueError("Published or actively rendering jobs cannot be cancelled")
        if job.status == "cancelled":
            return job
        job.status = "cancelled"
        job.stage = "cancelled"
        job.claimed_by = None
        job.lease_expires_at = None
        job.approval_version = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def revise_job(job_id: str) -> ContentJob:
    """Invalidate approval and queue a fresh script/render for operator revision."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status in {"published", "rendering", "cancelled"}:
            raise ValueError("This job cannot be revised in its current state")
        job.content_version += 1
        job.approval_version = None
        job.render_path = None
        job.status = "queued"
        job.stage = "topic"
        job.error_message = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def schedule_job(job_id: str, scheduled_at: datetime) -> ContentJob:
    if scheduled_at.tzinfo is None:
        raise ValueError("Scheduled time must include a timezone")
    when = scheduled_at.astimezone(timezone.utc)
    if when <= datetime.now(timezone.utc):
        raise ValueError("Scheduled time must be in the future")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status != "approved" or job.approval_version != job.content_version:
            raise ValueError("Approve the current Reel version before scheduling")
        job.scheduled_at = when
        job.schedule_notified_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def mark_due_schedules_notified() -> list[tuple[str, str, datetime]]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        jobs = list(
            session.scalars(
                select(ContentJob).where(
                    ContentJob.status == "approved",
                    ContentJob.approval_version == ContentJob.content_version,
                    ContentJob.scheduled_at.is_not(None),
                    ContentJob.scheduled_at <= now,
                    ContentJob.schedule_notified_at.is_(None),
                )
            )
        )
        result = []
        for job in jobs:
            scheduled_at = job.scheduled_at or now
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            result.append((job.id, job.topic, scheduled_at))
        return result


def mark_schedule_notified(job_id: str) -> None:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        scheduled_at = job.scheduled_at if job is not None else None
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if (
            job is not None
            and job.status == "approved"
            and job.approval_version == job.content_version
            and scheduled_at is not None
            and scheduled_at <= datetime.now(timezone.utc)
        ):
            job.schedule_notified_at = datetime.now(timezone.utc)
            session.commit()


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
        from .hq import get_daily_publish_limit, is_mock_publishing

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
        youtube_metadata = metadata.get("youtube") or {}
        instagram_metadata = metadata.get("instagram") or {}
        thumbnails = metadata.get("thumbnails") or {}
        keys = [
            f"{job.id}:{job.content_version}:{platform}"
            for platform in platforms
        ]
        existing_records = {
            record.idempotency_key: record
            for record in session.scalars(
                select(PublicationRecord).where(PublicationRecord.idempotency_key.in_(keys))
            )
        }
        day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        from sqlalchemy import func

        published_count = session.scalar(
            select(func.count(PublicationRecord.id)).where(
                PublicationRecord.status == "published",
                PublicationRecord.created_at >= day_start,
            )
        ) or 0
        pending_count = sum(
            1
            for key in keys
            if key not in existing_records or existing_records[key].status != "published"
        )
        if published_count + pending_count > get_daily_publish_limit():
            raise ValueError("Daily publication limit does not have room for all selected platforms")
        for platform in platforms:
            key = f"{job.id}:{job.content_version}:{platform}"
            record = existing_records.get(key)
            if record and record.status == "published":
                continue
            thumbnail_name = thumbnails.get(platform)
            thumbnail_path = None
            thumbnail_url = None
            if not is_mock_publishing():
                if not isinstance(thumbnail_name, str) or Path(thumbnail_name).name != thumbnail_name:
                    raise ValueError(f"{platform.title()} thumbnail is not ready")
                thumbnail_path = (Path(settings.media_root).resolve() / thumbnail_name).resolve()
                if thumbnail_path.parent != Path(settings.media_root).resolve() or not thumbnail_path.is_file():
                    raise ValueError(f"{platform.title()} thumbnail file is missing")
                if platform == "instagram":
                    if not settings.public_base_url:
                        raise ValueError("PUBLIC_BASE_URL is required to publish media thumbnails")
                    thumbnail_url = f"{settings.public_base_url.rstrip('/')}/media/{thumbnail_name}"
            platform_metadata = youtube_metadata if platform == "youtube" else instagram_metadata
            result = get_publisher(platform).publish(
                title=str(platform_metadata.get("title") or metadata["title"]),
                description=str(
                    platform_metadata.get("description")
                    or platform_metadata.get("caption")
                    or metadata["description"]
                ),
                media_path=job.render_path,
                tags=platform_metadata.get("tags", metadata.get("tags", [])),
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
                cover_url=thumbnail_url if platform == "instagram" else None,
            )
            if record is None:
                record = PublicationRecord(job_id=job.id, platform=platform, idempotency_key=key)
                session.add(record)
            record.status = result.status
            record.remote_id = result.remote_id
            record.remote_url = result.remote_url
            thumbnail_warning = "thumbnail setup needs attention" in result.detail.lower()
            record.error_message = (
                result.detail
                if result.status != "published" or thumbnail_warning
                else None
            )
            session.commit()
            if result.status == "published" and thumbnail_warning:
                job.error_message = result.detail
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


def worker_status() -> list[dict[str, object]]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        leases = list(session.scalars(select(WorkerLease).order_by(WorkerLease.last_seen_at.desc())))
        return [
            {
                "worker_id": lease.worker_id,
                "online": (
                    (now - lease.last_seen_at.replace(tzinfo=timezone.utc)).total_seconds()
                    <= max(120, get_settings().worker_poll_seconds * 4)
                ),
                "last_seen_at": lease.last_seen_at.isoformat(),
            }
            for lease in leases
        ]


def claim_render_job(worker_id: str) -> ContentJob | None:
    now = datetime.now(timezone.utc)
    lease_until = now.timestamp() + get_settings().worker_lease_seconds
    lease_expires = datetime.fromtimestamp(lease_until, timezone.utc)
    with SessionLocal() as session:
        if worker_id != "railway-renderer":
            return None

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
                    (ContentJob.status == "queued") & (ContentJob.stage == "render"),
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
