"""Automation engine: periodically turn top trends into generated jobs.

Automation target is selected from Telegram Controls. Safety: off by default
(AUTOMATION_ENABLED=false, toggleable at runtime from Telegram or the API),
does nothing while the factory is paused, and NEVER approves or publishes —
generated jobs still go through rendering, the quality gate and human approval.
"""
import asyncio
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import get_settings
from .db import FactorySetting, SessionLocal, TrendCandidate
from .hq import get_niche_label, get_publish_target
from .discovery import research_trending_topics
from .services import (
    create_job,
    generate_content,
    is_paused,
    mark_due_schedules_notified,
    mark_schedule_notified,
    requeue_failed_jobs,
)

logger = logging.getLogger(__name__)

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


def _research_angle(topic: str) -> dict[str, object]:
    """Ask the cheap research route for a hookable angle on a trending topic."""
    try:
        from .hq import get_niche_label
        from .scriptwriter import research_topic_angle

        return research_topic_angle(topic, get_niche_label())
    except Exception:
        logger.debug("Angle research unavailable for %s", topic, exc_info=True)
        return {}


def _facts_block(research: dict[str, object]) -> str:
    """Format researched angle and claims as verified facts for the writer."""
    if not research:
        return ""
    lines = []
    angle = str(research.get("angle") or "").strip()
    if angle:
        lines.append(f"Angle to develop: {angle}")
    claims = [str(item).strip() for item in (research.get("claims") or []) if str(item).strip()]
    if claims:
        lines.append("Verified points you may use (do not add anything else):")
        lines.extend(f"- {claim}" for claim in claims[:6])
    why_now = str(research.get("why_now") or "").strip()
    if why_now:
        lines.append(f"Why it matters now: {why_now}")
    return "\n".join(lines)


def run_automation_cycle() -> dict[str, object]:
    platform = get_publish_target()
    result: dict[str, object] = {
        "platform": platform,
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
    try:
        researched = research_trending_topics(get_niche_label(), limit=20)
        result["research_candidates_added"] = len(researched)
    except Exception:
        # Existing manually seeded candidates remain usable when the external
        # research feed is unavailable.
        logger.warning("Automation trend research unavailable", exc_info=True)
        result["research_candidates_added"] = 0

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
            job = create_job(topic, platform, request_id=f"trend:{trend_id}")
            result["jobs_created"] = int(result["jobs_created"]) + 1
            if job.status == "queued" and job.stage == "topic":
                research = _research_angle(topic)
                if research:
                    result["angles_researched"] = int(result.get("angles_researched", 0)) + 1
                generate_content(job.id, facts=_facts_block(research))
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
    next_automation_at = 0.0
    loop = asyncio.get_running_loop()
    while True:
        settings = get_settings()
        now = loop.time()
        if automation_enabled() and now >= next_automation_at:
            try:
                result = await asyncio.to_thread(run_automation_cycle)
                logger.info("Automation cycle: %s", result)
            except Exception as exc:
                logger.error("Automation cycle failed (%s)", type(exc).__name__)
            next_automation_at = now + max(30, settings.automation_interval_seconds)

        try:
            due = await asyncio.to_thread(mark_due_schedules_notified)
            if due and settings.telegram_bot_token and settings.owner_ids:
                from .telegram import _button, send_message

                for job_id, topic, scheduled_at in due:
                    for owner_id in settings.owner_ids:
                        await send_message(
                            owner_id,
                            (
                                f"⏰ <b>Your Reel is ready to publish</b>\n"
                                f"{topic}\n"
                                f"Scheduled reminder: {scheduled_at.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M IST')}\n"
                                "Publishing requires your explicit tap. No automatic post was made."
                            ),
                            {
                                "inline_keyboard": [[
                                    _button("📤 Publish approved Reel", f"publish:{job_id}")
                                ]]
                            },
                        )
                    mark_schedule_notified(job_id)
        except Exception as exc:
            logger.error(
                "Scheduled Telegram reminders failed (%s)",
                type(exc).__name__,
            )
        await asyncio.sleep(30)
