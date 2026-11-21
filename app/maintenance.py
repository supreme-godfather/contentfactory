import json
from pathlib import Path

from sqlalchemy import delete, func, select, update

from .config import get_settings
from .db import (
    ClipCandidate,
    ClipJob,
    ContentJob,
    FactorySetting,
    ProviderCredential,
    PublicationRecord,
    SessionLocal,
    TrendCandidate,
)


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
