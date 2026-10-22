"""Automation engine: periodically turn top trends into generated jobs.

YouTube-only for now (Instagram automation comes later). Safety: off by
default (AUTOMATION_ENABLED=false, toggleable at runtime from Telegram or
the API), does nothing while the factory is paused, and NEVER approves or
publishes — generated jobs still go through rendering, the quality gate
and human approval as usual.
"""
import asyncio
import logging

from sqlalchemy import select

from .config import get_settings
from .db import FactorySetting, SessionLocal, TrendCandidate
from .services import create_job, generate_content, is_paused, requeue_failed_jobs

logger = logging.getLogger(__name__)

# Automation is YouTube-only for now (user decision, 2026-10-22). Instagram
# automation will be added later; until then this is enforced in code, not
# just by configuration, so a stray AUTOMATION_PLATFORM value cannot create
# Instagram or cross-platform jobs.
AUTOMATION_PLATFORM = "youtube"


def automation_enabled() -> bool:
    """Runtime on/off state: Telegram/API toggle wins over the env default."""
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "automation_enabled")
        if setting is not None:
            return setting.value == "true"
    return get_settings().automation_enabled


def set_automation_enabled(enabled: bool) -> None:
    with SessionLocal() as session:
        setting = session.get(FactorySetting, "automation_enabled")
        if setting is None:
            session.add(FactorySetting(key="automation_enabled", value=str(enabled).lower()))
        else:
            setting.value = str(enabled).lower()
        session.commit()


def run_automation_cycle() -> dict[str, object]:
    result: dict[str, object] = {
        "platform": AUTOMATION_PLATFORM,
        "skipped_reason": None,
        "trends_considered": 0,
        "jobs_created": 0,
        "jobs_generated": 0,
        "jobs_requeued": 0,
    }
    if is_paused():
        result["skipped_reason"] = "factory_paused"
        return result

    settings = get_settings()
    result["jobs_requeued"] = requeue_failed_jobs(settings.automation_max_retries)

    if settings.automation_platform != AUTOMATION_PLATFORM:
        logger.warning(
            "AUTOMATION_PLATFORM=%s ignored: automation is YouTube-only for now",
            settings.automation_platform,
        )
    platform = AUTOMATION_PLATFORM

    with SessionLocal() as session:
        trends = list(
            session.scalars(
                select(TrendCandidate)
                .where(
                    TrendCandidate.status == "candidate",
                    TrendCandidate.total_score >= settings.automation_min_trend_score,
                    TrendCandidate.originality_score >= TrendCandidate.originality_required,
                )
                .order_by(TrendCandidate.total_score.desc(), TrendCandidate.created_at.asc())
                .limit(max(0, settings.automation_max_jobs_per_cycle))
            )
        )
        trend_ids_topics = [(trend.id, trend.topic) for trend in trends]
    result["trends_considered"] = len(trend_ids_topics)

    for trend_id, topic in trend_ids_topics:
        try:
            job = create_job(topic, platform)
            result["jobs_created"] = int(result["jobs_created"]) + 1
            generate_content(job.id)
            result["jobs_generated"] = int(result["jobs_generated"]) + 1
        except Exception:
            logger.exception("Automation: failed to generate job for trend %s; leaving it as candidate", trend_id)
            continue
        with SessionLocal() as session:
            trend = session.get(TrendCandidate, trend_id)
            if trend is not None:
                trend.status = "queued"
                session.commit()
    return result


async def automation_loop() -> None:
    while True:
        settings = get_settings()
        if automation_enabled():
            try:
                result = await asyncio.to_thread(run_automation_cycle)
                logger.info("Automation cycle: %s", result)
            except Exception:
                logger.exception("Automation cycle failed")
        await asyncio.sleep(max(30, settings.automation_interval_seconds))
