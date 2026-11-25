"""Multi-part story series.

One story, told in parts. A single model call produces the series bible (the
logline, the locked character, the setting and the beats of every part), the
engine creates one job per part, and the parts are released one at a time so
the audience that watched part 1 is still warm when part 2 lands.

The rules that keep a series honest:

- Part N cannot publish before part N-1 is live.
- Every part ends on the cliffhanger the plan promised.
- Part N+1 opens with a recap of part N, so a viewer who joined late can
  still follow the story.
- A series never spends more than its budget.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from .db import ContentJob, ContentSeries, SessionLocal
from .packs import Pack, get_pack

logger = logging.getLogger(__name__)

CADENCES: dict[str, dict[str, Any]] = {
    "daily": {"label": "1 part per day", "gap_hours": 24},
    "twice_daily": {"label": "2 parts per day", "gap_hours": 12},
    "manual": {"label": "I will publish each part myself", "gap_hours": 0},
    "auto": {"label": "Engine picks the gap from analytics", "gap_hours": 0},
}

DEFAULT_PART_COUNT = 6
DEFAULT_BUDGET_USD = 5.0
MAX_PART_COUNT = 12


@dataclass(frozen=True)
class PartPlan:
    index: int
    title: str
    beats: tuple[str, ...]
    cliffhanger: str
    teaser: str
    template_id: str = ""


@dataclass(frozen=True)
class SeriesPlan:
    topic: str
    pack_id: str
    part_count: int
    logline: str
    character: str
    setting: str
    parts: tuple[PartPlan, ...]


def _assign_templates(
    parts: list[PartPlan], topic: str, pack_id: str
) -> list[PartPlan]:
    """Give every part one of the repeatable scene templates."""
    from dataclasses import replace

    from .scene_templates import plan_scene_templates

    templates = plan_scene_templates(topic, pack_id, len(parts))
    return [
        replace(part, template_id=templates[index].template_id if index < len(templates) else "")
        for index, part in enumerate(parts)
    ]


def _fallback_plan(topic: str, pack: Pack, part_count: int) -> SeriesPlan:
    """A deterministic arc used when the model call cannot be trusted."""
    beats = pack.beats or ("hook", "problem", "disruption", "how_it_works", "truth_payoff")
    parts: list[PartPlan] = []
    for index in range(1, part_count + 1):
        is_last = index == part_count
        title = f"{topic} — भाग {index}" if index > 1 else f"{topic} — भाग 1"
        cliffhanger = (
            f"लेकिन असली खेल तो अब शुरू हुआ है।"
            if is_last
            else f"दरवाज़े पर आवाज़ आई… भाग {index + 1} में।"
        )
        parts.append(
            PartPlan(
                index=index,
                title=title,
                beats=tuple(beats[: min(4, len(beats))]),
                cliffhanger=cliffhanger,
                teaser="" if is_last else f"अगले भाग में पूरी तस्वीर सामने आएगी।",
            )
        )
    return SeriesPlan(
        topic=topic,
        pack_id=pack.pack_id,
        part_count=part_count,
        logline=f"{topic} — {part_count} भागों में एक कहानी।",
        character="एक शांत, आत्मविश्वासी किरदार जो हर मोड़ पर पहला है।",
        setting="आधी रात का शहर, गरम चाय का ठेला, महँगी चमक।",
        parts=tuple(_assign_templates(parts, topic, pack.pack_id)),
    )


def _plan_prompt(topic: str, pack: Pack, part_count: int) -> str:
    return (
        "You are the story editor for a Hindi vertical reel series.\n"
        f"Write a {part_count}-part story about: {topic}\n\n"
        "Return ONLY compact JSON with this exact shape:\n"
        '{"logline": "one Hindi sentence describing the whole story",'
        ' "character": "one Hindi sentence describing the recurring character,'
        ' personality and look",'
        ' "setting": "one Hindi sentence describing the world and locations",'
        ' "parts": [{"title": "Hindi part title",'
        ' "beats": ["beat one", "beat two", "beat three", "beat four"],'
        ' "cliffhanger": "the exact Hindi line that must be the last line of this part",'
        ' "teaser": "one Hindi line promising the next part"}]}\n\n'
        f"Style: {pack.label}. {pack.dialogue_style}\n"
        f"Beats per part, in order: {', '.join(pack.beats) or 'hook, conflict, escalation, twist'}.\n"
        "Rules: exactly one JSON object; the parts array must have exactly "
        f"{part_count} entries numbered from part 1 to part {part_count}; "
        "every cliffhanger must end part N and tease part N+1; the last part "
        "resolves the story; keep it family-safe and never violent."
    )


def plan_series(topic: str, pack: Pack, part_count: int = DEFAULT_PART_COUNT) -> SeriesPlan:
    """Plan the whole arc in one model call, with a deterministic fallback."""
    part_count = max(2, min(int(part_count), MAX_PART_COUNT))
    try:
        from .scriptwriter import minimax_text_key, _minimax_chat, _parse_json_object

        api_key = minimax_text_key()
        if not api_key:
            return _fallback_plan(topic, pack, part_count)
        raw = _minimax_chat(
            api_key,
            "You plan Hindi reel series. You always answer with valid JSON only.",
            _plan_prompt(topic, pack, part_count),
            max_tokens=2200,
            temperature=0.6,
        )
        payload = _parse_json_object(raw)
    except Exception:
        logger.warning("Series planning failed; using the deterministic arc", exc_info=True)
        return _fallback_plan(topic, pack, part_count)

    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or len(raw_parts) < part_count:
        logger.warning("Series plan returned %s parts; using the deterministic arc", len(raw_parts or []))
        return _fallback_plan(topic, pack, part_count)

    parts: list[PartPlan] = []
    for index, raw_part in enumerate(raw_parts[:part_count], start=1):
        if not isinstance(raw_part, dict):
            return _fallback_plan(topic, pack, part_count)
        beats = raw_part.get("beats")
        beats_tuple = tuple(
            " ".join(str(item).split())[:180]
            for item in (beats if isinstance(beats, list) else [])
            if str(item).strip()
        )
        cliffhanger = " ".join(str(raw_part.get("cliffhanger") or "").split())[:220]
        teaser = " ".join(str(raw_part.get("teaser") or "").split())[:220]
        title = " ".join(str(raw_part.get("title") or f"भाग {index}").split())[:120]
        if not cliffhanger:
            return _fallback_plan(topic, pack, part_count)
        parts.append(
            PartPlan(
                index=index,
                title=title,
                beats=beats_tuple or ("setup", "conflict", "turn", "reveal"),
                cliffhanger=cliffhanger,
                teaser=teaser,
            )
        )

    return SeriesPlan(
        topic=topic,
        pack_id=pack.pack_id,
        part_count=part_count,
        logline=" ".join(str(payload.get("logline") or "").split())[:400],
        character=" ".join(str(payload.get("character") or "").split())[:400],
        setting=" ".join(str(payload.get("setting") or "").split())[:400],
        parts=tuple(_assign_templates(parts, topic, pack.pack_id)),
    )


def _series_metadata(series: ContentSeries, plan: SeriesPlan, part: PartPlan) -> dict[str, Any]:
    pack = get_pack(series.pack_id)
    return {
        "series_id": series.id,
        "part_index": part.index,
        "part_count": series.part_count,
        "part_title": part.title,
        "part_beats": list(part.beats),
        "cliffhanger": part.cliffhanger,
        "teaser": part.teaser,
        "series_logline": plan.logline,
        "series_character": plan.character,
        "series_setting": plan.setting,
        "pack_id": pack.pack_id,
        "pack_label": pack.label,
        "series_caption": f"भाग {part.index}/{series.part_count}",
        "scene_template": part.template_id,
    }


def create_pack_reel(
    topic: str,
    pack_id: str,
    *,
    template_id: str | None = None,
    platform: str = "instagram",
    brand: str = "learn_with_elite_explainer",
    budget_usd: float = DEFAULT_BUDGET_USD,
) -> tuple[ContentSeries, list[ContentJob]]:
    """A single reel in a pack's style, with the same character machinery."""
    from .scene_templates import template_for

    pack = get_pack(pack_id)
    template = template_for(template_id) if template_id else None
    plan = plan_series(topic, pack, 1)
    if template is not None:
        from dataclasses import replace

        plan = SeriesPlan(
            topic=plan.topic,
            pack_id=plan.pack_id,
            part_count=1,
            logline=plan.logline,
            character=plan.character,
            setting=plan.setting,
            parts=(
                replace(
                    plan.parts[0],
                    template_id=template.template_id,
                    title=plan.parts[0].title,
                ),
            ),
        )
    return _create_series_jobs(plan, cadence="manual", budget_usd=budget_usd, platform=platform, brand=brand)


def _create_series_jobs(
    plan: SeriesPlan,
    *,
    cadence: str,
    budget_usd: float,
    platform: str,
    brand: str,
) -> tuple[ContentSeries, list[ContentJob]]:
    from .services import create_job

    clean_topic = " ".join(plan.topic.split()).strip()
    part_count = len(plan.parts)
    hours = int(CADENCES[cadence]["gap_hours"] or 0)

    with SessionLocal() as session:
        series = ContentSeries(
            topic=clean_topic,
            pack_id=plan.pack_id,
            part_count=part_count,
            status="planned",
            cadence=cadence,
            budget_usd=float(budget_usd),
            gap_hours=hours,
            plan_json=json.dumps(
                {
                    "logline": plan.logline,
                    "character": plan.character,
                    "setting": plan.setting,
                    "parts": [
                        {
                            "index": part.index,
                            "title": part.title,
                            "beats": list(part.beats),
                            "cliffhanger": part.cliffhanger,
                            "teaser": part.teaser,
                            "template_id": part.template_id,
                        }
                        for part in plan.parts
                    ],
                },
                ensure_ascii=False,
            ),
        )
        session.add(series)
        session.commit()
        session.refresh(series)

    jobs: list[ContentJob] = []
    for part in plan.parts:
        job = create_job(
            clean_topic,
            platform,
            brand,
            request_id=f"{series.id}:{part.index}",
        )
        with SessionLocal() as session:
            stored = session.get(ContentJob, job.id)
            if stored is None:
                continue
            stored.series_id = series.id
            stored.metadata_json = json.dumps(
                _series_metadata(series, plan, part), ensure_ascii=False
            )
            stored.status = "queued" if part.index == 1 else "held"
            stored.stage = "topic"
            stored.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(stored)
            jobs.append(stored)
    return series, jobs


def create_series(
    topic: str,
    pack_id: str,
    *,
    part_count: int = DEFAULT_PART_COUNT,
    cadence: str = "daily",
    budget_usd: float = DEFAULT_BUDGET_USD,
    platform: str = "instagram",
    brand: str = "learn_with_elite_explainer",
    gap_hours: int | None = None,
) -> tuple[ContentSeries, list[ContentJob]]:
    """Plan a series and create one job per part."""
    clean_topic = " ".join(topic.split()).strip()
    if not clean_topic:
        raise ValueError("A series needs a topic")
    if cadence not in CADENCES:
        raise ValueError("Unsupported series cadence")
    part_count = max(1, min(int(part_count), MAX_PART_COUNT))
    pack = get_pack(pack_id)
    plan = plan_series(clean_topic, pack, part_count)
    hours = gap_hours if gap_hours is not None else int(CADENCES[cadence]["gap_hours"] or 0)
    return _create_series_jobs(
        plan,
        cadence=cadence,
        budget_usd=budget_usd,
        platform=platform,
        brand=brand,
    )


def get_series(series_id: str) -> ContentSeries | None:
    with SessionLocal() as session:
        return session.get(ContentSeries, series_id)


def list_series(limit: int = 12) -> list[ContentSeries]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ContentSeries).order_by(ContentSeries.updated_at.desc()).limit(limit)
            )
        )


def series_jobs(series_id: str) -> list[ContentJob]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(ContentJob)
                .where(ContentJob.series_id == series_id)
                .order_by(ContentJob.created_at.asc())
            )
        )


def series_state(series_id: str) -> dict[str, Any]:
    """A JSON-serialisable view of a series for the API and Telegram."""
    series = get_series(series_id)
    if series is None:
        raise ValueError("Series not found")
    jobs = series_jobs(series_id)
    parts = []
    for job in jobs:
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        parts.append(
            {
                "job_id": job.id,
                "part_index": int(metadata.get("part_index") or 0),
                "title": metadata.get("part_title") or job.topic,
                "status": job.status,
                "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
                "published": job.status == "published",
            }
        )
    return {
        "id": series.id,
        "topic": series.topic,
        "pack_id": series.pack_id,
        "pack_label": get_pack(series.pack_id).label,
        "part_count": series.part_count,
        "status": series.status,
        "cadence": series.cadence,
        "cadence_label": CADENCES.get(series.cadence, {}).get("label", series.cadence),
        "budget_usd": series.budget_usd,
        "gap_hours": series.gap_hours,
        "character_asset": series.character_asset,
        "parts": parts,
        "created_at": series.created_at.isoformat() if series.created_at else None,
    }


def set_series_status(series_id: str, status: str) -> ContentSeries:
    if status not in {"planned", "producing", "releasing", "complete", "paused", "cancelled"}:
        raise ValueError("Unsupported series status")
    with SessionLocal() as session:
        series = session.get(ContentSeries, series_id)
        if series is None:
            raise ValueError("Series not found")
        series.status = status
        series.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(series)
        return series


def release_next_part(series_id: str, *, now: datetime | None = None) -> ContentJob | None:
    """Unlock the part that follows the last published one."""
    now = now or datetime.now(timezone.utc)
    series = get_series(series_id)
    if series is None:
        raise ValueError("Series not found")
    jobs = series_jobs(series_id)
    if not jobs:
        return None
    published = [job for job in jobs if job.status == "published"]
    if not published:
        return None
    last_published = max(published, key=lambda job: job.updated_at or datetime.min.replace(tzinfo=timezone.utc))
    following = [job for job in jobs if job.id != last_published.id and job.status not in {"published", "cancelled"}]
    if not following:
        with SessionLocal() as session:
            stored = session.get(ContentSeries, series_id)
            if stored is not None:
                stored.status = "complete"
                stored.updated_at = now
                session.commit()
        return None
    next_job = min(following, key=lambda job: job.created_at)
    if next_job.status == "held":
        gap = series.gap_hours if series.cadence != "auto" else _auto_gap_hours()
        with SessionLocal() as session:
            stored = session.get(ContentJob, next_job.id)
            if stored is None:
                return None
            stored.status = "queued"
            stored.stage = "topic"
            if gap:
                base = last_published.updated_at or now
                stored.scheduled_at = base + timedelta(hours=gap)
            stored.updated_at = now
            session.commit()
            session.refresh(stored)
        with SessionLocal() as session:
            stored_series = session.get(ContentSeries, series_id)
            if stored_series is not None:
                stored_series.status = "releasing"
                stored_series.updated_at = now
                session.commit()
        return stored
    return next_job


def _auto_gap_hours() -> int:
    """Gap chosen from the channel's own watch-time performance."""
    try:
        from .discovery import analytics_summary

        rows = analytics_summary(limit=20)
        watched = [
            row.get("watch_seconds", 0) / max(row.get("views", 1), 1)
            for row in rows
            if row.get("views")
        ]
        if watched:
            average = sum(watched) / len(watched)
            if average < 12:
                return 18
            if average < 25:
                return 22
        return 24
    except Exception:
        return 24


def publish_order_ok(job: ContentJob) -> bool:
    """True when every earlier part of the series is already published."""
    if not job.series_id:
        return True
    jobs = series_jobs(job.series_id)
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    part_index = int(metadata.get("part_index") or 1)
    for other in jobs:
        if other.id == job.id:
            continue
        try:
            other_metadata = json.loads(other.metadata_json or "{}")
        except json.JSONDecodeError:
            other_metadata = {}
        other_index = int(other_metadata.get("part_index") or 0)
        if other_index and other_index < part_index and other.status != "published":
            return False
    return True


def earlier_part(job: ContentJob) -> ContentJob | None:
    """The part that must be published before this one."""
    if not job.series_id:
        return None
    try:
        metadata = json.loads(job.metadata_json or "{}")
    except json.JSONDecodeError:
        return None
    part_index = int(metadata.get("part_index") or 1)
    candidates = []
    for other in series_jobs(job.series_id):
        if other.id == job.id:
            continue
        try:
            other_metadata = json.loads(other.metadata_json or "{}")
        except json.JSONDecodeError:
            other_metadata = {}
        other_index = int(other_metadata.get("part_index") or 0)
        if other_index and other_index < part_index:
            candidates.append(other)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(
        (json.loads(item.metadata_json or "{}") if item.metadata_json else {}).get("part_index") or 0
    ))
