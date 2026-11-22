import json
from pathlib import Path

from sqlalchemy import delete, func, select, update

from .config import get_settings
from .db import (
    ClipCandidate,
    ClipJob,
    ContentJob,
    ContentSeries,
    FactorySetting,
    ProviderCredential,
    PublicationRecord,
    SessionLocal,
    TrendCandidate,
)


def clear_broken_rendered_jobs() -> dict[str, int]:
    """Remove rendered jobs whose media file no longer exists.

    These jobs cannot be published because their MP4 was lost, so they only
    block the queue. Published jobs and any job with its media are untouched.
    """
    from sqlalchemy import select as _select

    media_root = Path(get_settings().media_root).resolve()
    removed = 0
    with SessionLocal() as session:
        candidates = list(
            session.scalars(
                _select(ContentJob).where(
                    ContentJob.status.in_({"awaiting_approval", "approved"})
                )
            )
        )
        for job in candidates:
            render_name = Path(job.render_path).name if job.render_path else ""
            if not render_name:
                continue
            if (media_root / render_name).is_file():
                continue
            session.delete(job)
            removed += 1
        session.commit()
    return {"broken_render_jobs_removed": removed}


def reset_factory_content() -> dict[str, int]:
    """Remove every content job, clip job and generated media file.

    Credentials, accounts, provider configuration, brand settings, audio
    catalogue and the auto/quality settings are preserved. Only produced
    content is removed.
    """
    media_root = Path(get_settings().media_root).resolve()
    filenames: set[str] = set()
    removed_jobs = 0
    with SessionLocal() as session:
        for job in list(session.scalars(select(ContentJob))):
            if job.render_path:
                filenames.add(Path(job.render_path).name)
            try:
                metadata = json.loads(job.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            for name in (metadata.get("assets") or {}).values():
                if isinstance(name, str):
                    filenames.add(name)
            for name in (metadata.get("thumbnails") or {}).values():
                if isinstance(name, str):
                    filenames.add(name)
            session.delete(job)
            removed_jobs += 1
        for candidate in list(session.scalars(select(ClipCandidate))):
            filenames.add(candidate.render_filename)
            filenames.add(candidate.youtube_thumbnail_filename)
            filenames.add(candidate.instagram_thumbnail_filename)
            session.delete(candidate)
        for clip_job in list(session.scalars(select(ClipJob))):
            session.delete(clip_job)
        for series in list(session.scalars(select(ContentSeries))):
            if series.character_asset:
                filenames.add(series.character_asset)
            session.delete(series)
        session.commit()
    deleted_files = 0
    for name in filenames:
        if Path(name).name != name or name.startswith("audio-"):
            continue
        path = (media_root / name).resolve()
        if path.parent == media_root and path.is_file():
            path.unlink()
            deleted_files += 1
    return {
        "content_jobs_removed": removed_jobs,
        "media_files_removed": deleted_files,
    }


def delete_all_published_content() -> dict[str, object]:
    """Delete every published video from YouTube and Instagram.

    Deletion uses each platform's official API with the stored credentials and
    only touches content this factory published. The publication records are
    marked as deleted so the factory never republishes the same item.
    """
    import httpx

    from .platform_credentials import get_platform_secret
    from .youtube_oauth import get_valid_access_token

    settings = get_settings()
    version = settings.instagram_graph_version or "v27.0"
    results: list[dict[str, object]] = []
    with SessionLocal() as session:
        records = list(
            session.scalars(
                select(PublicationRecord).where(
                    PublicationRecord.status.in_({"published", "delete_failed"})
                )
            )
        )
        youtube_token = None
        instagram_token = get_platform_secret("instagram") or settings.instagram_access_token
        for record in records:
            remote_id = (record.remote_id or "").strip()
            entry: dict[str, object] = {
                "job_id": record.job_id,
                "platform": record.platform,
                "remote_id": remote_id,
            }
            if not remote_id:
                entry["status"] = "skipped_no_remote_id"
                results.append(entry)
                continue
            try:
                if record.platform == "youtube":
                    if youtube_token is None:
                        youtube_token = get_valid_access_token()
                    with httpx.Client(timeout=60) as client:
                        response = client.delete(
                            "https://www.googleapis.com/youtube/v3/videos",
                            params={"video_id": remote_id},
                            headers={"Authorization": f"Bearer {youtube_token}"},
                        )
                    entry["status"] = "deleted" if response.status_code in {200, 204} else f"failed_{response.status_code}"
                    if response.status_code >= 400:
                        entry["detail"] = response.text[:200]
                elif record.platform == "instagram":
                    if not instagram_token:
                        entry["status"] = "failed_missing_instagram_token"
                        results.append(entry)
                        continue
                    with httpx.Client(timeout=60) as client:
                        response = client.delete(
                            f"https://graph.facebook.com/{version}/{remote_id}",
                            headers={"Authorization": f"Bearer {instagram_token}"},
                        )
                    entry["status"] = "deleted" if response.status_code in {200, 204} else f"failed_{response.status_code}"
                    if response.status_code >= 400:
                        entry["detail"] = response.text[:200]
                else:
                    entry["status"] = "skipped_unknown_platform"
            except Exception as exc:
                entry["status"] = "failed"
                entry["detail"] = type(exc).__name__
            record.status = "deleted" if entry.get("status") == "deleted" else "delete_failed"
            record.error_message = str(entry.get("detail") or entry.get("status"))[:300]
            results.append(entry)
        session.commit()
    return {
        "attempted": len(results),
        "deleted": sum(1 for item in results if item.get("status") == "deleted"),
        "failed": sum(1 for item in results if str(item.get("status", "")).startswith("failed")),
        "results": results,
    }


def clear_script_review_backlog() -> dict[str, int]:
    """Remove jobs that are only waiting for a script review.

    These are jobs created before auto mode existed. They are not rendering and
    not published, so removing them frees the queue without touching any
    published record, credential, account or media file.
    """
    from sqlalchemy import select as _select

    removed = 0
    with SessionLocal() as session:
        backlog = list(
            session.scalars(
                _select(ContentJob).where(
                    ContentJob.status.in_({"awaiting_script_approval", "drafting_script"})
                )
            )
        )
        for job in backlog:
            session.delete(job)
            removed += 1
        session.commit()
    return {"script_review_jobs_removed": removed}


def clear_pending_queues(confirmation: str) -> dict[str, int]:
    if confirmation != "CLEAR_PENDING_QUEUES":
        raise ValueError("Queue cleanup confirmation is invalid")
    media_root = Path(get_settings().media_root).resolve()
    media_files: set[str] = set()
    with SessionLocal() as session:
        content_jobs = list(
            session.scalars(select(ContentJob).where(ContentJob.status != "published"))
        )
        content_ids = [job.id for job in content_jobs]
        for job in content_jobs:
            render_path = Path(job.render_path).resolve() if job.render_path else None
            if render_path and render_path.parent == media_root:
                media_files.add(render_path.name)
            try:
                metadata = json.loads(job.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            names = list((metadata.get("assets") or {}).values())
            names.extend((metadata.get("thumbnails") or {}).values())
            for name in names:
                if (
                    isinstance(name, str)
                    and Path(name).name == name
                    and not name.startswith("audio-")
                ):
                    media_files.add(name)

        candidates = list(session.scalars(select(ClipCandidate)))
        for candidate in candidates:
            media_files.update({
                candidate.render_filename,
                candidate.youtube_thumbnail_filename,
                candidate.instagram_thumbnail_filename,
            })

        publication_count = 0
        if content_ids:
            publication_result = session.execute(
                delete(PublicationRecord).where(
                    PublicationRecord.job_id.in_(content_ids),
                    PublicationRecord.status != "published",
                )
            )
            publication_count = publication_result.rowcount or 0
            session.execute(delete(ContentJob).where(ContentJob.id.in_(content_ids)))
        candidate_count = len(candidates)
        clip_job_count = session.scalar(select(func.count(ClipJob.id))) or 0
        session.execute(delete(ClipCandidate))
        session.execute(delete(ClipJob))
        session.execute(
            update(TrendCandidate)
            .where(TrendCandidate.status == "queued")
            .values(status="candidate")
        )
        removed_provider_state = 0
        external_credential = session.get(ProviderCredential, "agent_router")
        if external_credential is not None:
            session.delete(external_credential)
            removed_provider_state += 1
        external_model = session.get(FactorySetting, "agent_router_model")
        if external_model is not None:
            session.delete(external_model)
            removed_provider_state += 1
        script_provider = session.get(FactorySetting, "provider_script")
        if script_provider is not None and script_provider.value == "agent_router":
            script_provider.value = "template"
            removed_provider_state += 1
        session.commit()

    deleted_files = 0
    for name in media_files:
        if Path(name).name != name:
            continue
        path = (media_root / name).resolve()
        if path.parent == media_root and path.is_file():
            path.unlink()
            deleted_files += 1
    return {
        "content_jobs": len(content_ids),
        "clip_jobs": int(clip_job_count),
        "clip_candidates": candidate_count,
        "publication_records": publication_count,
        "media_files": deleted_files,
        "external_provider_state": removed_provider_state,
    }
