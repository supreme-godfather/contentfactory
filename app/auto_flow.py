"""Resolve which genre a job should use, and audit what the engine did.

The owner picks the mode in Telegram:

- ``specific``  — one chosen genre
- ``shuffle``   — a random genre inside the chosen category
- ``random``    — a random genre from every category
- ``auto``      — the best match for the topic, chosen deterministically
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import AutomationEvent, FactorySetting, SessionLocal

logger = logging.getLogger(__name__)


def resolve_genre(topic: str) -> tuple[dict[str, str], str]:
    """Return the genre for a topic plus a short reason for the audit log."""
    from .genres import get_genre, match_genre_for_topic, random_genre
    from .hq import get_genre_id, get_genre_mode

    mode = get_genre_mode()
    if mode == "specific":
        genre = get_genre(get_genre_id())
        if genre:
            return genre, "owner selected genre"
        mode = "auto"
    if mode == "shuffle":
        genre = random_genre()
        return genre, f"shuffled {genre['category']}"
    if mode == "random":
        genre = random_genre()
        return genre, "random genre"
    genre = match_genre_for_topic(topic)
    return genre, "matched to topic"


def record_automation_event(kind: str, job_id: str, detail: str) -> None:
    """Append a short, secret-free audit line for the auto pipeline.

    Audit writes never raise: a logging failure must not stop production work.
    """
    try:
        with SessionLocal() as session:
            session.add(AutomationEvent(
                kind=str(kind)[:40],
                job_id=str(job_id)[:36],
                detail=" ".join(str(detail).split())[:400],
            ))
            session.commit()
    except Exception:
        logger.warning("Could not record automation event %s", kind, exc_info=True)


def recent_automation_events(limit: int = 12) -> list[str]:
    try:
        with SessionLocal() as session:
            rows = list(
                session.scalars(
                    select(AutomationEvent).order_by(AutomationEvent.created_at.desc()).limit(limit)
                )
            )
    except Exception:
        return []
    events = []
    for row in reversed(rows):
        stamp = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        events.append(f"{stamp} | {row.kind} | {row.job_id[:8]} | {row.detail[:120]}")
    return events


def next_asset_job() -> tuple[str, int] | None:
    """Find the next approved script waiting for media generation."""
    from .db import ContentJob

    try:
        with SessionLocal() as session:
            job = session.scalar(
                select(ContentJob)
                .where(ContentJob.status == "queued", ContentJob.stage == "assets")
                .order_by(ContentJob.created_at.asc())
                .limit(1)
            )
            if job is None:
                return None
            try:
                metadata = json.loads(job.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if metadata.get("script_approval_version") != job.content_version:
                return None
            return job.id, job.content_version
    except Exception:
        logger.warning("Could not read the asset queue", exc_info=True)
        return None


async def asset_pipeline_loop(concurrency: int = 3, interval: float = 10.0) -> None:
    """Generate media for approved scripts in the background.

    Provider video generation takes minutes per clip, so the work must never sit
    inside an HTTP request. Several jobs run in parallel to keep a batch of ten
    videos moving, and each job is claimed atomically so nothing is generated
    twice.
    """
    import asyncio

    from .services import generate_assets

    running: dict[str, asyncio.Task[object]] = {}

    async def _run(job_id: str, version: int) -> None:
        try:
            await asyncio.to_thread(generate_assets, job_id, version)
            record_automation_event("assets_generated", job_id, "queued for render")
        except Exception as exc:
            record_automation_event("assets_failed", job_id, type(exc).__name__)
            logger.warning("Asset generation failed for %s: %s", job_id, exc)

    while True:
        try:
            for job_id, task in list(running.items()):
                if task.done():
                    task.result()
                    running.pop(job_id, None)
            while len(running) < max(1, concurrency):
                found = next_asset_job()
                if found is None:
                    break
                job_id, version = found
                if job_id in running:
                    break
                running[job_id] = asyncio.create_task(_run(job_id, version))
                await asyncio.sleep(2)
        except Exception:
            logger.warning("Asset pipeline loop error", exc_info=True)
        await asyncio.sleep(max(2, interval))


async def publish_retry_loop(interval: float = 1200.0) -> None:
    """Retry publications that a platform temporarily refused.

    YouTube limits how many videos an unverified channel may upload per day, so
    a refused upload is retried later instead of being lost. Only the blocked
    platform is retried, and nothing is published that was not already
    approved by the quality gate.
    """
    import asyncio

    from .db import ContentJob, PublicationRecord
    from .services import publish_job

    while True:
        try:
            await asyncio.sleep(max(60.0, interval))
            with SessionLocal() as session:
                blocked = list(
                    session.scalars(
                        select(PublicationRecord).where(
                            PublicationRecord.status == "blocked"
                        )
                    )
                )
                candidates: dict[str, list[str]] = {}
                for record in blocked:
                    job = session.get(ContentJob, record.job_id)
                    if job is None or job.status != "approved":
                        continue
                    candidates.setdefault(record.job_id, []).append(record.platform)
            for job_id, platforms in candidates.items():
                try:
                    publish_job(job_id, only_platforms=platforms)
                except Exception:
                    logger.debug("Publish retry still blocked for %s", job_id)
        except Exception:
            logger.warning("Publish retry loop error", exc_info=True)


async def script_pipeline_loop(interval: float = 20.0) -> None:
    """Draft scripts for queued work so the factory keeps moving on its own.

    Series parts are drafted as soon as they are released. Other queued jobs are
    picked up after a short delay, so a job created from Telegram or the API is
    never raced by this loop.
    """
    import asyncio

    from .db import ContentJob
    from .services import generate_content

    while True:
        try:
            await asyncio.sleep(max(5.0, interval))
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=90)
            with SessionLocal() as session:
                pending = list(
                    session.scalars(
                        select(ContentJob)
                        .where(
                            ContentJob.status == "queued",
                            ContentJob.stage == "topic",
                        )
                        .order_by(ContentJob.created_at.asc())
                        .limit(3)
                    )
                )
            for job in pending:
                created = job.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if job.series_id is None and created is not None and created > cutoff:
                    continue
                try:
                    await asyncio.to_thread(generate_content, job.id)
                except Exception:
                    logger.debug("Script pipeline skipped %s", job.id)
        except Exception:
            logger.warning("Script pipeline loop error", exc_info=True)


async def scheduled_publish_loop(interval: float = 180.0) -> None:
    """Publish approved jobs whose scheduled time has arrived.

    Series parts are produced ahead of their release window so the audience of
    the previous part is still warm when the next one lands. This loop only
    publishes what the quality gate already approved, and only when the owner
    has enabled automatic publishing.
    """
    import asyncio

    from .db import ContentJob
    from .hq import is_auto_publish_enabled
    from .services import publish_job

    while True:
        try:
            await asyncio.sleep(max(60.0, interval))
            if not is_auto_publish_enabled():
                continue
            now = datetime.now(timezone.utc)
            with SessionLocal() as session:
                due = list(
                    session.scalars(
                        select(ContentJob).where(
                            ContentJob.status == "approved",
                            ContentJob.approval_version == ContentJob.content_version,
                            ContentJob.scheduled_at.is_not(None),
                            ContentJob.scheduled_at <= now,
                        )
                    )
                )
            for job in due:
                try:
                    publish_job(job.id)
                except Exception:
                    logger.debug("Scheduled publish still waiting for %s", job.id)
        except Exception:
            logger.warning("Scheduled publish loop error", exc_info=True)


def create_trend_batch(count: int = 10) -> list[str]:
    from .automation import research_trending_topics
    from .db import TrendCandidate
    from .hq import get_niche_label, get_publish_target
    from .services import create_job, generate_content

    limit = max(1, min(30, int(count)))
    try:
        research_trending_topics(get_niche_label(), limit=limit * 3)
    except Exception:
        logger.warning("Trend research unavailable for the batch", exc_info=True)
    with SessionLocal() as session:
        candidates = list(
            session.scalars(
                select(TrendCandidate)
                .where(TrendCandidate.status == "candidate")
                .order_by(TrendCandidate.total_score.desc())
                .limit(limit)
            )
        )
    created: list[str] = []
    for candidate in candidates:
        job = create_job(
            candidate.topic,
            get_publish_target(),
            request_id=f"batch:{candidate.id}",
        )
        if job.status == "queued" and job.stage == "topic":
            try:
                generate_content(job.id)
                record_automation_event("batch_script_drafted", job.id, candidate.topic[:60])
                created.append(job.id)
            except Exception as exc:
                record_automation_event("batch_script_failed", job.id, type(exc).__name__)
    return created
