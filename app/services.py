import json
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from sqlalchemy import or_, select, text, update

from .config import get_settings
from .db import ContentJob, ContentSeries, FactorySetting, PublicationRecord, SessionLocal, WorkerLease
from .google_media import create_job_assets
from .media_qc import evaluate_report
from .publishers import get_publisher
from .quality import validate_content
from .scriptwriter import build_script, build_script_package
from .storyboard import (
    STYLE_ANCHOR,
    build_storyboard,
    caption_cues,
    estimate_duration_seconds,
)

_RENDER_CLAIM_LOCK_ID = 0x48434652434C414D


def _watermark_plan() -> dict[str, object]:
    from .hq import get_watermark_text, is_watermark_enabled

    return {
        "enabled": is_watermark_enabled(),
        "text": get_watermark_text(),
    }


def _resolve_job(session, job_id: str) -> ContentJob:
    identifier = (job_id or "").strip()
    if not identifier:
        raise ValueError("Job not found")

    job = session.get(ContentJob, identifier)
    if job is not None:
        return job
    if len(identifier) < 8 or not re.fullmatch(r"[0-9a-fA-F-]+", identifier):
        raise ValueError("Job not found")

    matches = list(
        session.scalars(
            select(ContentJob)
            .where(ContentJob.id.startswith(identifier.lower()))
            .limit(2)
        )
    )
    if not matches:
        raise ValueError("Job not found")
    if len(matches) > 1:
        raise ValueError("Job ID is ambiguous; provide more characters")
    return matches[0]


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


def _apply_ai_publishing_package(
    metadata: dict[str, object],
    package_metadata: dict[str, object],
    format_key: str,
) -> dict[str, object]:
    """Overlay AI-written titles, description, hashtags and cover concept."""
    if not package_metadata:
        metadata["title_options"] = [metadata.get("title")]
        return metadata

    title_variants = [
        str(item).strip()[:100]
        for item in (package_metadata.get("title_variants") or [])
        if str(item).strip()
    ]
    if title_variants:
        metadata["title_options"] = title_variants
        metadata["title"] = title_variants[0]

    description = str(package_metadata.get("description") or "").strip()
    hashtags = [
        str(item).strip()[:40]
        for item in (package_metadata.get("hashtags") or [])
        if str(item).strip()
    ]
    if description:
        metadata["description"] = description[:1200]
    if hashtags:
        metadata["hashtags"] = hashtags[:12]

    title = str(metadata.get("title") or "")
    description = str(metadata.get("description") or "")
    tags = list(metadata.get("tags") or [])
    series_caption = str(metadata.get("series_caption") or "").strip()
    if series_caption:
        title = f"{title} ({series_caption})"[:100]
        description = f"{series_caption}\n\n{description}"
    metadata["youtube"] = {
        "title": title[:100],
        "description": f"{description}\n\n{' '.join(hashtags[:3])}" if hashtags else description,
        "tags": tags,
    }
    metadata["instagram"] = {
        "caption": (f"{title}\n\n{description}\n\n{' '.join(hashtags)}")[:2200] if hashtags else f"{title}\n\n{description}",
        "hashtags": hashtags or list(metadata.get("hashtags") or []),
    }
    hook_type = str(package_metadata.get("hook_type") or "")[:24]
    if hook_type:
        metadata["hook_type"] = hook_type
    hook_variants = [
        " ".join(str(item).split())[:220]
        for item in (package_metadata.get("hook_variants") or [])
        if str(item).strip()
    ]
    if hook_variants:
        metadata["hook_variants"] = hook_variants[:3]
    concept = str(package_metadata.get("thumbnail_concept") or "")[:300]
    if concept:
        metadata["thumbnail_concept"] = concept
    return metadata


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
                package = build_script_package(
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
                stored.script_text = str(package.get("script") or "")
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
                metadata["script_approval_version"] = None
                metadata["assets"] = {}
                stored.metadata_json = json.dumps(metadata, ensure_ascii=False)
                stored.stage = "script"
                stored.status = "awaiting_script_approval"
                stored.updated_at = datetime.now(timezone.utc)
                session.commit()
                session.refresh(stored)
                job = stored
        jobs.append(job)
    return jobs


def _mark_generation_failed(job_id: str, version: int, stage: str, exc: Exception) -> None:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None or job.content_version != version or job.stage != stage:
            return
        job.status = "failed"
        job.error_message = f"{type(exc).__name__}: generation failed during {stage}"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()


def generate_content(job_id: str, facts: str = "") -> ContentJob:
    """Draft the current script package without spending on media providers."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status == "awaiting_script_approval" and job.stage == "script":
            return job
        if job.status != "queued" or job.stage != "topic":
            raise ValueError("Job is not available for script generation")
        version = job.content_version
        topic = job.topic
        brand = job.brand
        claim = session.execute(
            update(ContentJob)
            .where(
                ContentJob.id == job_id,
                ContentJob.content_version == version,
                ContentJob.status == "queued",
                ContentJob.stage == "topic",
            )
            .values(
                status="drafting_script",
                stage="script",
                error_message=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if claim.rowcount != 1:
            session.rollback()
            raise ValueError("Job script generation is already in progress")
        session.commit()

    try:
        from .hq import get_format, get_format_key, get_niche_label

        format_key = get_format_key()
        format_info = get_format()
        niche_label = get_niche_label()
        from .auto_flow import resolve_genre
        from .packs import get_pack

        series_context = _series_context_for_job(job_id)
        pack_id = str(series_context.get("pack_id") or "") or None
        if pack_id is None:
            from .hq import get_active_pack_id

            pack_id = get_active_pack_id()
        pack = get_pack(pack_id)
        genre, genre_reason = resolve_genre(topic)
        package = build_script_package(
            topic,
            brand,
            format=format_key,
            niche=niche_label,
            facts=facts,
            genre=genre,
            pack_id=pack_id,
            series_context=series_context or None,
        )
        script = str(package.get("script") or "")
        package_metadata = package.get("metadata") or {}
        storyboard = build_storyboard(topic, script)
        cues = caption_cues(script)
        estimated_seconds = estimate_duration_seconds(script)
        if not cues or any(
            not isinstance(cue.get("text"), str)
            or not cue["text"].strip()
            or not cue["text"].strip("0 ").strip()
            for cue in cues
            if isinstance(cue, dict)
        ):
            raise RuntimeError("Caption generation produced invalid or placeholder text.")
        metadata = {
            **_build_publish_metadata(
                topic=topic,
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
            "style_anchor": pack.visual_anchor or STYLE_ANCHOR,
            "pack_id": pack.pack_id,
            "pack_label": pack.label,
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
            "script_approval_version": None,
            "assets": {},
            "watermark": _watermark_plan(),
            "genre": genre,
            "genre_reason": genre_reason,
        }
        metadata = _apply_ai_publishing_package(metadata, package_metadata, format_key)
        if series_context:
            metadata.update(
                {
                    key: value
                    for key, value in series_context.items()
                    if key not in {"assets", "assets_version"}
                }
            )
        if package.get("critic", {}).get("applied"):
            metadata["script_polished"] = True
    except Exception as exc:
        _mark_generation_failed(job_id, version, "script", exc)
        raise

    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if (
            job is None
            or job.content_version != version
            or job.status != "drafting_script"
            or job.stage != "script"
        ):
            raise ValueError("Job changed while its script was being generated")
        job.script_text = script
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        job.status = "awaiting_script_approval"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
    return _maybe_auto_advance(job, version)


def _maybe_auto_advance(job: ContentJob, version: int) -> ContentJob:
    """Approve the script and build media without owner action in auto mode."""
    from .auto_flow import record_automation_event
    from .hq import is_auto_mode_enabled

    if not is_auto_mode_enabled():
        return job
    try:
        approved = approve_script(job.id, 0, version)
        record_automation_event("auto_approved_script", job.id, f"v{version}")
        generated = generate_assets(job.id, version)
        record_automation_event("auto_generated_media", job.id, "queued for render")
        return generated
    except Exception as exc:
        record_automation_event("auto_pipeline_stopped", job.id, type(exc).__name__)
        logger.warning("Auto pipeline stopped for %s: %s", job.id, exc)
        return job


def approve_script(job_id: str, approver_id: int, expected_version: int) -> ContentJob:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.content_version != expected_version:
            raise ValueError("Script approval is stale; review the current version")
        if job.status != "awaiting_script_approval" or job.stage != "script":
            raise ValueError("Job script is not awaiting approval")
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Job script metadata is invalid") from exc
        metadata["script_approval_version"] = expected_version
        metadata["script_approved_by"] = approver_id
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        job.status = "queued"
        job.stage = "assets"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def _series_context_for_job(job_id: str) -> dict[str, object]:
    """The series context of a job: its part, its neighbours and its ending."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if job is None or not job.series_id:
            return {}
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        part_index = int(metadata.get("part_index") or 1)
        previous_recap = str(metadata.get("previous_recap") or "")
    if part_index > 1 and not previous_recap:
        previous_recap = _previous_part_recap(job.series_id, part_index)
    context = {
        key: value
        for key, value in metadata.items()
        if key
        in {
            "series_id",
            "part_index",
            "part_count",
            "part_title",
            "part_beats",
            "cliffhanger",
            "teaser",
            "series_logline",
            "series_character",
            "series_setting",
            "pack_id",
            "pack_label",
            "series_caption",
        }
    }
    if previous_recap:
        context["previous_recap"] = previous_recap
    return context


def _previous_part_recap(series_id: str, part_index: int) -> str:
    """One line that reminds the viewer what happened in the previous part."""
    from .series import series_jobs

    for other in series_jobs(series_id):
        try:
            other_metadata = json.loads(other.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        other_index = int(other_metadata.get("part_index") or 0)
        if other_index != part_index - 1:
            continue
        title = str(other_metadata.get("part_title") or "").strip()
        script = (other.script_text or "").strip()
        if script:
            first_line = script.split("\n\n")[0].strip()
            return (first_line or title)[:220]
        return title[:220]
    return ""


def _series_character_reference(
    job_id: str,
    metadata: dict[str, object],
    media_root: Path,
) -> Path | None:
    """Return the character sheet for a series, generating it once if needed.

    The sheet is the first frame of every generated shot, so the same character
    appears in every scene of every part of the story.
    """
    series_id = metadata.get("series_id")
    if not isinstance(series_id, str) or not series_id:
        return None
    from .packs import get_pack
    from .series import get_series

    series = get_series(series_id)
    if series is None:
        return None
    pack = get_pack(series.pack_id)
    if not pack.needs_character_image:
        return None
    media_root.mkdir(parents=True, exist_ok=True)
    if series.character_asset:
        existing = media_root / series.character_asset
        if existing.is_file():
            return existing
    try:
        from .google_media import generate_minimax_image

        filename = f"{series_id}-character.jpg"
        destination = media_root / filename
        generate_minimax_image(pack.character_prompt, destination)
    except Exception as exc:
        logger.warning("Character sheet generation failed: %s", exc)
        return None
    with SessionLocal() as session:
        stored = session.get(ContentSeries, series_id)
        if stored is not None:
            stored.character_asset = filename
            stored.updated_at = datetime.now(timezone.utc)
            session.commit()
    return destination


def generate_assets(job_id: str, expected_version: int) -> ContentJob:
    """Generate paid assets only for an explicitly approved script version."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.content_version != expected_version:
            raise ValueError("Asset generation request is stale")
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Job script metadata is invalid") from exc
        if metadata.get("script_approval_version") != expected_version:
            raise ValueError("Approve the current script before generating media")
        if job.status != "queued" or job.stage != "assets":
            raise ValueError("Job assets are not available for generation")
        script = job.script_text or ""
        topic = job.topic
        storyboard = metadata.get("storyboard") or []
        format_code = str(metadata.get("format") or "")
        claim = session.execute(
            update(ContentJob)
            .where(
                ContentJob.id == job_id,
                ContentJob.content_version == expected_version,
                ContentJob.status == "queued",
                ContentJob.stage == "assets",
            )
            .values(
                status="generating_assets",
                error_message=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if claim.rowcount != 1:
            session.rollback()
            raise ValueError("Job asset generation is already in progress")
        session.commit()

    media_root = Path(get_settings().media_root)
    assets: dict[str, str] = {}
    audio_plan: dict[str, object] = {}
    from .cost import enforce_cost_ceiling

    enforce_cost_ceiling(job_id, metadata, script)
    if metadata.get("series_id"):
        from .cost import enforce_series_budget

        enforce_series_budget(str(metadata["series_id"]))
    try:
        from .job_inputs import get_custom_voiceover

        custom_voiceover = get_custom_voiceover(metadata)
        character_reference = _series_character_reference(job_id, metadata, media_root)
        assets = create_job_assets(
            job_id,
            topic,
            script,
            media_root,
            storyboard=storyboard if isinstance(storyboard, list) else [],
            thumbnail_prompt=str(metadata.get("thumbnail_prompt") or "") or None,
            custom_voiceover=custom_voiceover or None,
            character_reference=character_reference,
            character_prompt=str(metadata.get("series_character") or "") or None,
        )
        from .audio_catalog import get_default_audio_asset

        bgm = get_default_audio_asset(media_root, "bgm")
        if bgm is not None:
            filename = bgm.get("filename")
            if isinstance(filename, str) and Path(filename).name == filename:
                assets["bgm"] = filename
                audio_plan["bgm_asset_id"] = bgm.get("id")
                audio_plan["bgm_gain"] = 0.16
        from .provider_credentials import get_provider_for_role, media_generation_enabled

        video_provider = get_provider_for_role("video")
        voice_provider = get_provider_for_role("voice")
        if (
            media_generation_enabled()
            and video_provider in {"minimax", "higgsfield"}
            and not any(key.startswith("scene_video_") for key in assets)
        ):
            raise RuntimeError(
                f"{video_provider} was selected, but no provider-generated video asset was created."
            )
        if (
            format_code in {"instagram_reel", "youtube_short"}
            and media_generation_enabled()
            and voice_provider != "off"
            and "voiceover" not in assets
        ):
            raise RuntimeError(
                f"{voice_provider} narration was selected, but no voiceover asset was created."
            )
    except Exception as exc:
        _mark_generation_failed(job_id, expected_version, "assets", exc)
        raise

    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if (
            job is None
            or job.content_version != expected_version
            or job.status != "generating_assets"
            or job.stage != "assets"
        ):
            for filename in assets.values():
                if Path(filename).name == filename:
                    (media_root / filename).unlink(missing_ok=True)
            raise ValueError("Job changed while its assets were being generated")
        current_metadata = json.loads(job.metadata_json or "{}")
        if current_metadata.get("script_approval_version") != expected_version:
            raise ValueError("Script approval changed while assets were being generated")
        current_metadata["assets"] = assets
        current_metadata["assets_version"] = expected_version
        current_metadata["video_provider"] = video_provider
        current_metadata["voice_provider"] = voice_provider
        current_metadata["audio_plan"] = audio_plan
        current_metadata["caption_timing_source"] = (
            "provider_alignment" if "voice_timing" in assets else "estimated"
        )
        job.metadata_json = json.dumps(current_metadata, ensure_ascii=False)
        job.stage = "render"
        job.status = "queued"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def list_jobs(limit: int = 25) -> list[ContentJob]:
    with SessionLocal() as session:
        return list(session.scalars(select(ContentJob).order_by(ContentJob.created_at.desc()).limit(limit)))


def approve_job(
    job_id: str,
    approver_id: int,
    expected_version: int | None = None,
) -> ContentJob:
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if job.status != "awaiting_approval":
            raise ValueError("Job must finish rendering before approval")
        if expected_version is not None and job.content_version != expected_version:
            raise ValueError("Video approval is stale; review the current version")
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
        if job.stage == "script":
            job.stage = "topic"
        elif job.stage == "assets":
            try:
                metadata = json.loads(job.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            job.stage = (
                "assets"
                if metadata.get("script_approval_version") == job.content_version
                else "topic"
            )
        else:
            job.stage = "render" if (job.script_text and job.metadata_json) else "topic"
        job.error_message = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.retry_count = (job.retry_count or 0) + 1
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
        return job


def cancel_job(job_id: str, expected_version: int | None = None) -> ContentJob:
    """Cancel work that has not been published or is actively rendering."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if expected_version is not None and job.content_version != expected_version:
            raise ValueError("Job cancellation is stale; review the current version")
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


def revise_job(job_id: str, expected_version: int | None = None) -> ContentJob:
    """Invalidate approval and queue a fresh script/render for operator revision."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job:
            raise ValueError("Job not found")
        if expected_version is not None and job.content_version != expected_version:
            raise ValueError("Script revision is stale; review the current version")
        if job.status in {"published", "rendering", "cancelled"}:
            raise ValueError("This job cannot be revised in its current state")
        job.content_version += 1
        job.approval_version = None
        job.render_path = None
        if job.metadata_json:
            try:
                metadata = json.loads(job.metadata_json)
            except json.JSONDecodeError:
                metadata = {}
            metadata["script_approval_version"] = None
            metadata["assets"] = {}
            metadata.pop("assets_version", None)
            job.metadata_json = json.dumps(metadata, ensure_ascii=False)
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


def publish_job(job_id: str, only_platforms: list[str] | None = None) -> ContentJob:
    settings = get_settings()
    with SessionLocal() as session:
        job = _resolve_job(session, job_id)
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
        if job.series_id:
            from .series import earlier_part, publish_order_ok

            if not publish_order_ok(job):
                earlier = earlier_part(job)
                earlier_index = 0
                if earlier is not None:
                    try:
                        earlier_metadata = json.loads(earlier.metadata_json or "{}")
                    except json.JSONDecodeError:
                        earlier_metadata = {}
                    earlier_index = int(earlier_metadata.get("part_index") or 0)
                raise ValueError(
                    f"This is part of a story series: publish part {earlier_index} first."
                )
        platforms = ["youtube", "instagram"] if job.platform == "both" else [job.platform]
        if only_platforms:
            allowed = {
                item.strip().lower()
                for item in only_platforms
                if item.strip().lower() in {"youtube", "instagram"}
            }
            platforms = [item for item in platforms if item in allowed]
            if not platforms:
                raise ValueError("No requested platform is valid for this job")
        metadata = json.loads(job.metadata_json or "{}")
        if getattr(settings, "publish_preflight_enabled", True):
            from .preflight import qc_problems, run_preflight

            problems = run_preflight(
                job,
                metadata,
                platforms,
                mock_publishing=is_mock_publishing(),
                settings=settings,
            )
            problems.extend(
                qc_problems(
                    metadata,
                    required=bool(getattr(settings, "media_qc_required", False)),
                )
            )
            if problems:
                raise ValueError(
                    "Preflight blocked publication: " + ", ".join(sorted(set(problems)))
                )
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
    if job.series_id:
        _release_next_series_part(job.series_id)
    return job


def _release_next_series_part(series_id: str) -> None:
    """Unlock the next part of a story after the current one went out."""
    try:
        from .series import release_next_part

        release_next_part(series_id)
    except Exception:
        logger.warning("Could not release the next series part for %s", series_id, exc_info=True)


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

        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(CAST(:lock_id AS BIGINT))"),
                {"lock_id": _RENDER_CLAIM_LOCK_ID},
            )

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
        candidate_query = (
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
        if dialect_name == "postgresql":
            candidate_query = candidate_query.with_for_update(skip_locked=True)
        job = session.scalar(candidate_query)
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
        if getattr(get_settings(), "media_qc_required", False):
            try:
                metadata = json.loads(job.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            report = metadata.get("media_qc")
            if not isinstance(report, dict):
                raise ValueError("The render was not measured; media quality control is required")
            qc_blockers, _qc_warnings = evaluate_report(report)
            if qc_blockers:
                raise ValueError(
                    "Media quality control rejected the render: " + ", ".join(qc_blockers)
                )
        job.render_path = render_path
        job.stage = "quality_check"
        job.status = "awaiting_approval"
        job.claimed_by = None
        job.lease_expires_at = None
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(job)
    return _maybe_auto_publish(job)


def record_media_qc(job_id: str, worker_id: str, report: dict) -> dict[str, object]:
    """Store the media measurements a worker produced for a render."""
    with SessionLocal() as session:
        job = session.get(ContentJob, job_id)
        if not job or job.status != "rendering" or job.claimed_by != worker_id:
            raise ValueError("Job is not currently rendering")
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["media_qc"] = report
        metadata["media_qc_at"] = datetime.now(timezone.utc).isoformat()
        job.metadata_json = json.dumps(metadata, ensure_ascii=False)
        job.updated_at = datetime.now(timezone.utc)
        session.commit()
    blockers, warnings = evaluate_report(report)
    return {
        "blockers": blockers,
        "warnings": warnings,
        "ok": not blockers,
    }


def _maybe_auto_publish(job: ContentJob) -> ContentJob:
    """Publish a rendered job automatically when the owner enabled auto-publish."""
    from .auto_flow import record_automation_event
    from .hq import is_auto_publish_enabled

    if not is_auto_publish_enabled():
        return job
    if job.platform not in {"youtube", "instagram", "both"}:
        return job
    if job.scheduled_at is not None:
        scheduled_at = job.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at > datetime.now(timezone.utc):
            return job
    try:
        if job.status == "awaiting_approval":
            job = approve_job(job.id, 0, job.content_version)
        publish_job(job.id)
    except ValueError as exc:
        record_automation_event("auto_publish_blocked", job.id, str(exc)[:160])
        logger.warning("Auto publish blocked for %s: %s", job.id, exc)
        return job
    except Exception as exc:
        record_automation_event("auto_publish_failed", job.id, type(exc).__name__)
        logger.warning("Auto publish failed for %s: %s", job.id, exc)
        return job
    with SessionLocal() as session:
        published = session.get(ContentJob, job.id)
    record_automation_event("auto_published", job.id, f"platform={job.platform}")
    return published or job


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
