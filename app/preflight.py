"""Preflight checks that run immediately before a publication attempt.

A professional pipeline fails fast and loudly: if the media is not measured, the
thumbnail is missing, the platform credential expired or the same topic already
went out today, the operator is told while they are watching instead of after a
half-finished upload.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .db import ContentJob, PublicationRecord, SessionLocal
from .media_qc import qc_findings

DUPLICATE_WINDOW_HOURS = 24


def _credential_problem(platform: str, settings: Any) -> str | None:
    """Report a missing credential for a platform that is switched on."""
    if platform == "youtube":
        if not getattr(settings, "youtube_enabled", False):
            return None
        from .youtube_oauth import get_valid_access_token

        try:
            get_valid_access_token()
        except Exception:
            return "youtube_credential_missing"
        return None
    if platform == "instagram":
        if not getattr(settings, "instagram_enabled", False):
            return None
        from .platform_credentials import get_platform_secret

        token = get_platform_secret("instagram") or getattr(settings, "instagram_access_token", "")
        user_id = getattr(settings, "instagram_user_id", "")
        if not token or not user_id:
            return "instagram_credential_missing"
    return None


def _recent_duplicate(job: ContentJob) -> bool:
    window = datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_WINDOW_HOURS)
    with SessionLocal() as session:
        recent = session.scalars(
            select(PublicationRecord)
            .join(ContentJob, ContentJob.id == PublicationRecord.job_id)
            .where(
                ContentJob.topic == job.topic,
                ContentJob.id != job.id,
                PublicationRecord.status == "published",
                PublicationRecord.created_at >= window,
            )
            .limit(1)
        ).first()
    return recent is not None


def run_preflight(
    job: ContentJob,
    metadata: dict[str, Any],
    platforms: list[str],
    *,
    mock_publishing: bool,
    settings: Any = None,
) -> list[str]:
    """Return the list of preflight problems for a publication attempt."""
    settings = settings if settings is not None else get_settings()
    problems: list[str] = []

    if not mock_publishing:
        if not job.render_path:
            problems.append("render_missing")
        else:
            media_path = Path(str(job.render_path))
            if not media_path.is_file():
                problems.append("render_file_missing")
            elif media_path.stat().st_size < 1024:
                problems.append("render_file_too_small")

    if getattr(job, "status", "") == "published":
        problems.append("already_published")

    if _recent_duplicate(job):
        problems.append("duplicate_topic_published_recently")

    for platform in platforms:
        credential_problem = _credential_problem(platform, settings)
        if credential_problem:
            problems.append(credential_problem)

    if not mock_publishing:
        thumbnails = metadata.get("thumbnails") or {}
        for platform in platforms:
            thumbnail_name = thumbnails.get(platform)
            if not isinstance(thumbnail_name, str) or Path(thumbnail_name).name != thumbnail_name:
                problems.append(f"{platform}_thumbnail_missing")
        if "instagram" in platforms and not getattr(settings, "public_base_url", ""):
            problems.append("public_base_url_missing")

    return problems


def qc_problems(metadata: dict[str, Any], *, required: bool) -> list[str]:
    """Media QC findings for a job, as preflight problems."""
    blockers, _warnings = qc_findings(metadata)
    if not blockers:
        return []
    if not required and blockers == ["media_not_measured"]:
        return []
    return [f"media_qc:{item}" for item in blockers]
