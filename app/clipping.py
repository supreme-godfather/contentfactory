import hashlib
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import func, or_, select, text

from .config import get_settings
from .db import ClipCandidate, ClipJob, ContentJob, SessionLocal
from .encryption import get_fernet

_CLIP_CLAIM_LOCK_ID = 0x484346434C49504D
_CLIP_WORKER_ID = "railway-clipper"


def validate_clip_source_url(value: str) -> str:
    clean = value.strip()
    if not clean or len(clean) > 2048 or any(ord(char) < 32 for char in clean):
        raise ValueError("Clip source URL must contain 1-2048 safe characters")
    try:
        url = httpx.URL(clean)
    except httpx.InvalidURL as exc:
        raise ValueError("Clip source must be a valid HTTPS URL") from exc
    host = (url.host or "").encode("idna").decode("ascii").lower().rstrip(".")
    if (
        url.scheme != "https"
        or not host
        or url.username
        or url.password
        or url.fragment
        or (url.port is not None and url.port != 443)
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith((".local", ".internal", ".localhost"))
        or "." not in host
    ):
        raise ValueError("Clip source must be a public HTTPS URL without credentials or a fragment")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Clip source URL must use a public hostname, not an IP literal")
    return str(url)


def _source_display(source_url: str) -> str:
    parts = urlsplit(source_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path[:420], "", ""))[:500]


def create_clip_job(
    source_url: str,
    source_kind: str,
    *,
    capture_seconds: int | None = None,
    request_id: str | None = None,
) -> ClipJob:
    source_url = validate_clip_source_url(source_url)
    if source_kind not in {"vod", "live"}:
        raise ValueError("Clip source kind must be vod or live")
    if source_kind == "vod":
        capture_seconds = None
    else:
        maximum = max(90, get_settings().max_clip_capture_seconds)
        capture_seconds = int(capture_seconds or min(900, maximum))
        if not 90 <= capture_seconds <= maximum:
            raise ValueError(f"Live capture must be between 90 and {maximum} seconds")
    fingerprint = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    scope = f"{source_kind}:{fingerprint}:{request_id or ''}"
    key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    encrypted = get_fernet().encrypt(source_url.encode("utf-8")).decode("ascii")
    with SessionLocal() as session:
        existing = session.scalar(select(ClipJob).where(ClipJob.idempotency_key == key))
        if existing is not None:
            return existing
        job = ClipJob(
            source_url_encrypted=encrypted,
            source_display=_source_display(source_url),
            source_kind=source_kind,
            capture_seconds=capture_seconds,
            idempotency_key=key,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def list_clip_jobs(limit: int = 20) -> list[ClipJob]:
    with SessionLocal() as session:
        return list(session.scalars(select(ClipJob).order_by(ClipJob.created_at.desc()).limit(limit)))


def list_clip_candidates(job_id: str) -> list[ClipCandidate]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ClipCandidate)
                .where(ClipCandidate.job_id == job_id)
                .order_by(ClipCandidate.rank.asc())
            )
        )


def claim_clip_job(worker_id: str) -> tuple[ClipJob, str, str] | None:
    if worker_id != _CLIP_WORKER_ID:
        return None
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(CAST(:lock_id AS BIGINT))"),
                {"lock_id": _CLIP_CLAIM_LOCK_ID},
            )
        query = (
            select(ClipJob)
            .where(
                or_(
                    ClipJob.status == "queued",
                    (ClipJob.status == "processing")
                    & (
                        ClipJob.lease_expires_at.is_(None)
                        | (ClipJob.lease_expires_at < now)
                    ),
                )
            )
            .order_by(ClipJob.created_at.asc())
            .limit(1)
        )
        if session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = session.scalar(query)
        if job is None:
            return None
        lease_token = secrets.token_urlsafe(32)
        job.status = "processing"
        job.stage = "ingest"
        job.claimed_by = worker_id
        job.attempt = (job.attempt or 0) + 1
        job.lease_token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        job.lease_expires_at = now + timedelta(seconds=get_settings().clip_worker_lease_seconds)
        job.error_message = None
        job.updated_at = now
        session.commit()
        session.refresh(job)
        source_url = get_fernet().decrypt(job.source_url_encrypted.encode("ascii")).decode("utf-8")
        return job, lease_token, source_url


def _get_leased_job(session, job_id: str, worker_id: str, lease_token: str) -> ClipJob:
    job = session.get(ClipJob, job_id)
    token_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    lease_expires_at = job.lease_expires_at if job is not None else None
    if lease_expires_at is not None and lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    if (
        job is None
        or job.status != "processing"
        or job.claimed_by != worker_id
        or not job.lease_token_hash
        or not secrets.compare_digest(job.lease_token_hash, token_hash)
        or lease_expires_at is None
        or lease_expires_at < now
    ):
        raise ValueError("Clip lease is invalid or expired")
    return job


def renew_clip_lease(job_id: str, worker_id: str, lease_token: str, stage: str) -> ClipJob:
    if stage not in {"ingest", "analyse", "render", "upload"}:
        raise ValueError("Invalid clipping stage")
    with SessionLocal() as session:
        job = _get_leased_job(session, job_id, worker_id, lease_token)
        job.stage = stage
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=get_settings().clip_worker_lease_seconds
        )
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def register_clip_candidate(
    job_id: str,
    worker_id: str,
    lease_token: str,
    *,
    rank: int,
    start_ms: int,
    end_ms: int,
    score: int,
    title: str,
    reason: str,
    transcript_excerpt: str,
    filename: str,
    youtube_thumbnail_filename: str,
    instagram_thumbnail_filename: str,
    checksum: str,
    size_bytes: int,
) -> ClipCandidate:
    if rank not in {1, 2, 3}:
        raise ValueError("Clip rank must be 1, 2, or 3")
    if type(start_ms) is not int or type(end_ms) is not int:
        raise ValueError("Clip timestamps must be integer milliseconds")
    duration = end_ms - start_ms
    if start_ms < 0 or not 15000 <= duration <= 60000:
        raise ValueError("Clip duration must be between 15 and 60 seconds")
    if not 0 <= score <= 1000:
        raise ValueError("Clip score must be between 0 and 1000")
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise ValueError("Clip filename is invalid")
    for thumbnail in (youtube_thumbnail_filename, instagram_thumbnail_filename):
        if Path(thumbnail).name != thumbnail or not thumbnail.endswith(".jpg"):
            raise ValueError("Clip thumbnail filename is invalid")
    if not (len(checksum) == 64 and all(char in "0123456789abcdef" for char in checksum)):
        raise ValueError("Clip checksum is invalid")
    clean_title = " ".join(title.split())[:200] or f"Clip {rank}"
    with SessionLocal() as session:
        _get_leased_job(session, job_id, worker_id, lease_token)
        existing = session.scalar(
            select(ClipCandidate).where(
                ClipCandidate.job_id == job_id,
                ClipCandidate.rank == rank,
            )
        )
        if existing is not None:
            if existing.sha256 == checksum:
                return existing
            raise ValueError("Clip rank already has a different artifact")
        candidates = list(
            session.scalars(select(ClipCandidate).where(ClipCandidate.job_id == job_id))
        )
        for candidate in candidates:
            if max(start_ms, candidate.start_ms) < min(end_ms, candidate.end_ms):
                raise ValueError("Clip candidates must not overlap")
        candidate = ClipCandidate(
            job_id=job_id,
            rank=rank,
            start_ms=start_ms,
            end_ms=end_ms,
            score=score,
            title=clean_title,
            reason=" ".join(reason.split())[:500],
            transcript_excerpt=transcript_excerpt[:3000],
            render_filename=filename,
            youtube_thumbnail_filename=youtube_thumbnail_filename,
            instagram_thumbnail_filename=instagram_thumbnail_filename,
            sha256=checksum,
            size_bytes=size_bytes,
        )
        session.add(candidate)
        session.commit()
        session.refresh(candidate)
        return candidate


def complete_clip_job(
    job_id: str,
    worker_id: str,
    lease_token: str,
    source_metadata: dict[str, object],
) -> ClipJob:
    with SessionLocal() as session:
        job = _get_leased_job(session, job_id, worker_id, lease_token)
        count = session.scalar(
            select(func.count(ClipCandidate.id)).where(ClipCandidate.job_id == job_id)
        ) or 0
        if count != 3:
            raise ValueError("Exactly three clip candidates are required")
        job.status = "awaiting_review"
        job.stage = "review"
        job.metadata_json = json.dumps(source_metadata, ensure_ascii=False)
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def fail_clip_job(job_id: str, worker_id: str, lease_token: str, error_code: str) -> ClipJob:
    clean_error = " ".join(error_code.split())[:200]
    with SessionLocal() as session:
        job = _get_leased_job(session, job_id, worker_id, lease_token)
        job.status = "failed"
        job.error_message = clean_error or "clip_processing_failed"
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def retry_clip_job(job_id: str) -> ClipJob:
    media_root = Path(get_settings().media_root)
    with SessionLocal() as session:
        job = session.get(ClipJob, job_id)
        if job is None:
            raise ValueError("Clip job not found")
        if job.status != "failed":
            raise ValueError("Only failed clip jobs can be retried")
        candidates = list(
            session.scalars(select(ClipCandidate).where(ClipCandidate.job_id == job_id))
        )
        filenames = []
        for candidate in candidates:
            filenames.extend([
                candidate.render_filename,
                candidate.youtube_thumbnail_filename,
                candidate.instagram_thumbnail_filename,
            ])
            session.delete(candidate)
        job.status = "queued"
        job.stage = "ingest"
        job.claimed_by = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.error_message = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
    for filename in filenames:
        if Path(filename).name == filename:
            (media_root / filename).unlink(missing_ok=True)
    return job


def review_clip_candidate(candidate_id: str, decision: str, reviewer_id: int) -> ClipCandidate:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Clip review decision must be approved or rejected")
    with SessionLocal() as session:
        candidate = session.get(ClipCandidate, candidate_id)
        if candidate is None:
            raise ValueError("Clip candidate not found")
        if candidate.status not in {"awaiting_review", decision}:
            raise ValueError("Clip candidate was already reviewed")
        candidate.status = decision
        candidate.reviewed_by = str(reviewer_id)
        candidate.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(candidate)
        return candidate


def promote_clip_candidate(
    candidate_id: str,
    platform: str,
    reviewer_id: int,
) -> ContentJob:
    if platform not in {"youtube", "instagram", "both"}:
        raise ValueError("Platform must be youtube, instagram, or both")
    with SessionLocal() as session:
        candidate = session.get(ClipCandidate, candidate_id)
        if candidate is None:
            raise ValueError("Clip candidate not found")
        if candidate.status not in {"approved", "promoted"}:
            raise ValueError("Approve the clip candidate before promotion")
        clip_job = session.get(ClipJob, candidate.job_id)
        if clip_job is None:
            raise ValueError("Clip job not found")
        from .services import _build_publish_metadata, create_job

        content_job = create_job(
            candidate.title,
            platform,
            brand="agency_clipper",
            request_id=f"clip:{candidate.id}",
        )
        stored = session.get(ContentJob, content_job.id)
        if stored is None:
            raise ValueError("Promoted content job was not created")
        if stored.status == "approved" and stored.approval_version == stored.content_version:
            return stored
        publish_metadata = _build_publish_metadata(
            topic=candidate.title,
            format_key="reel",
            niche_label="Agency clipping",
        )
        metadata = {
            **publish_metadata,
            "content_type": "clip",
            "clip_candidate_id": candidate.id,
            "clip_job_id": candidate.job_id,
            "duration_seconds": round((candidate.end_ms - candidate.start_ms) / 1000, 2),
            "approval_required": True,
            "disclosure": "Source clip edited by the Content Factory clipping engine.",
            "thumbnails": {
                "youtube": candidate.youtube_thumbnail_filename,
                "instagram": candidate.instagram_thumbnail_filename,
            },
            "caption_cues": [],
            "assets": {},
        }
        stored.script_text = candidate.transcript_excerpt or candidate.title
        stored.metadata_json = json.dumps(metadata, ensure_ascii=False)
        stored.render_path = str((Path(get_settings().media_root) / candidate.render_filename).resolve())
        stored.status = "approved"
        stored.stage = "quality_check"
        stored.approval_version = stored.content_version
        stored.updated_at = datetime.now(timezone.utc)
        candidate.status = "promoted"
        candidate.reviewed_by = str(reviewer_id)
        session.commit()
        session.refresh(stored)
        return stored
