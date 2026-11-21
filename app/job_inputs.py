"""Owner-supplied inputs for a single content job.

The factory can generate a script, but the owner may own a better script, a
recorded voice-over, or a designed cover. These helpers store those inputs on
the job without touching any other job, keep `content_version` honest, and
never spend provider credits by themselves.
"""
import json
from pathlib import Path

from .db import ContentJob, SessionLocal
from .storyboard import build_storyboard, caption_cues, estimate_duration_seconds


def _load_metadata(job: ContentJob) -> dict[str, object]:
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Job script metadata is invalid") from exc
    return metadata if isinstance(metadata, dict) else {}


def _save_metadata(job: ContentJob, metadata: dict[str, object]) -> None:
    job.metadata_json = json.dumps(metadata, ensure_ascii=False)


def set_custom_script(job_id: str, expected_version: int, script_text: str) -> ContentJob:
    """Replace the generated draft with the owner's own script.

    The storyboard, caption cues, and duration are rebuilt from the new text so
    rendering stays consistent. Approval is cleared because the content changed.
    """
    clean = " ".join(script_text.split())
    if len(clean) < 40:
        raise ValueError("Custom script must contain at least 40 characters")
    if len(clean) > 8000:
        raise ValueError("Custom script is too long")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None:
            raise ValueError("Job not found")
        if job.content_version != expected_version:
            raise ValueError("Custom script request is stale; review the current version")
        if job.status not in {"awaiting_script_approval", "queued"} or job.stage not in {"script", "assets"}:
            raise ValueError("This job cannot take a custom script in its current state")
        metadata = _load_metadata(job)
        job.content_version = expected_version + 1
        job.script_text = clean
        storyboard = build_storyboard(job.topic, clean)
        cues = caption_cues(clean)
        metadata["storyboard"] = storyboard
        metadata["caption_cues"] = cues
        metadata["story_arc"] = [scene["stage"] for scene in storyboard]
        metadata["duration_seconds"] = estimate_duration_seconds(clean)
        metadata["script_approval_version"] = None
        metadata["custom_script"] = True
        metadata["assets"] = {}
        metadata.pop("assets_version", None)
        metadata.pop("custom_voiceover", None)
        _save_metadata(job, metadata)
        job.status = "awaiting_script_approval"
        job.stage = "script"
        job.error_message = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.render_path = None
        from datetime import datetime, timezone

        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def set_custom_voiceover(job_id: str, expected_version: int, filename: str) -> ContentJob:
    """Use an owner-recorded voice-over instead of a provider narration."""
    if Path(filename).name != filename or not filename.endswith(".mp3"):
        raise ValueError("Custom voice-over filename is invalid")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None:
            raise ValueError("Job not found")
        if job.content_version != expected_version:
            raise ValueError("Custom voice-over request is stale")
        if job.status not in {"awaiting_script_approval", "queued"} or job.stage not in {"script", "assets"}:
            raise ValueError("This job cannot take a custom voice-over in its current state")
        metadata = _load_metadata(job)
        metadata["custom_voiceover"] = filename
        _save_metadata(job, metadata)
        session.commit()
        session.refresh(job)
        return job


def set_custom_cover(job_id: str, platform: str, filename: str) -> ContentJob:
    """Replace a platform cover with an owner-designed image."""
    if platform not in {"youtube", "instagram"}:
        raise ValueError("Unsupported cover platform")
    if Path(filename).name != filename or not filename.endswith(".jpg"):
        raise ValueError("Custom cover filename is invalid")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None:
            raise ValueError("Job not found")
        metadata = _load_metadata(job)
        covers = dict(metadata.get("thumbnails") or {})
        covers[platform] = filename
        metadata["thumbnails"] = covers
        metadata.setdefault("custom_covers", []).append(platform)
        _save_metadata(job, metadata)
        session.commit()
        session.refresh(job)
        return job


def get_custom_voiceover(metadata: dict[str, object]) -> str:
    value = metadata.get("custom_voiceover")
    if isinstance(value, str) and Path(value).name == value:
        return value
    return ""
