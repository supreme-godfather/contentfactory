"""Hook variants: alternative openings for a script.

The script package proposes three opening lines. The owner can swap the opening
before any media credit is spent, which is the cheapest retention lever in the
whole pipeline. The first paragraph of the narration is replaced with the
chosen hook and the job returns to script review so the new version is
approved like any other.
"""
import json
from datetime import datetime, timezone

from .db import ContentJob, SessionLocal

MAX_HOOK_VARIANTS = 3


def hook_variants(metadata: dict) -> list[str]:
    """Return the hook variants stored on a job's metadata."""
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("hook_variants")
    if not isinstance(raw, list):
        return []
    variants = [" ".join(str(item).split())[:220] for item in raw if str(item).strip()]
    return variants[:MAX_HOOK_VARIANTS]


def _replace_opening(script: str, hook: str) -> str:
    parts = [part for part in script.split("\n\n") if part.strip()]
    if not parts:
        return hook
    parts[0] = hook
    return "\n\n".join(parts)


def apply_hook_variant(job_id: str, index: int) -> ContentJob:
    """Use one of the proposed hooks as the opening of the script."""
    if index < 0 or index >= MAX_HOOK_VARIANTS:
        raise ValueError("Hook variant is out of range")
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status not in {"awaiting_script_approval", "drafting_script"}:
            raise ValueError("Hooks can only be changed while the script is in review")
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        variants = hook_variants(metadata)
        if index >= len(variants):
            raise ValueError("This job has no such hook variant")
        hook = variants[index]
        script = job.script_text or ""
        if not script.strip():
            raise ValueError("This job has no script to change")
        job.script_text = _replace_opening(script, hook)
        metadata["selected_hook"] = hook
        metadata["selected_hook_index"] = index
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        job.content_version += 1
        job.approval_version = None
        job.status = "awaiting_script_approval"
        job.stage = "script_review"
        job.error_message = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job
