"""Pipeline health snapshot.

One call answers the questions an operator actually asks before letting a
factory run unattended: are the workers alive, what is stuck, what did we spend
today, are the platform credentials usable, and did the last renders pass the
delivery profile.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .config import get_settings
from .db import ContentJob, PublicationRecord, SessionLocal
from .media_qc import evaluate_report

ACTIVE_STATUSES = ("queued", "scripting", "script_review", "rendering", "awaiting_approval", "approved")


def _queue_depth() -> dict[str, int]:
    with SessionLocal() as session:
        rows = session.execute(
            select(ContentJob.status, func.count(ContentJob.id)).group_by(ContentJob.status)
        ).all()
    return {str(status): int(count) for status, count in rows}


def _publications_today() -> tuple[int, int]:
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as session:
        published = session.scalar(
            select(func.count(PublicationRecord.id)).where(
                PublicationRecord.status == "published",
                PublicationRecord.created_at >= day_start,
            )
        ) or 0
        failed = session.scalar(
            select(func.count(PublicationRecord.id)).where(
                PublicationRecord.status != "published",
                PublicationRecord.created_at >= day_start,
            )
        ) or 0
    return int(published), int(failed)


def _recent_failures(limit: int = 5) -> list[dict[str, object]]:
    with SessionLocal() as session:
        jobs = list(
            session.scalars(
                select(ContentJob)
                .where(ContentJob.status.in_(("failed", "error")))
                .order_by(ContentJob.updated_at.desc())
                .limit(limit)
            )
        )
    return [
        {
            "id": job.id[:8],
            "topic": job.topic,
            "stage": job.stage,
            "error": (job.error_message or "")[:160],
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        for job in jobs
    ]


def _qc_summary(limit: int = 20) -> dict[str, object]:
    with SessionLocal() as session:
        jobs = list(
            session.scalars(
                select(ContentJob)
                .where(ContentJob.render_path.is_not(None))
                .order_by(ContentJob.updated_at.desc())
                .limit(limit)
            )
        )
    measured = 0
    passed = 0
    for job in jobs:
        try:
            metadata = json.loads(job.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        report = metadata.get("media_qc")
        if not isinstance(report, dict):
            continue
        measured += 1
        blockers, _warnings = evaluate_report(report)
        if not blockers:
            passed += 1
    return {"measured": measured, "passed": passed, "sampled": len(jobs)}


def _provider_status() -> dict[str, object]:
    settings = get_settings()
    from .platform_credentials import get_platform_secret

    youtube_ready = False
    try:
        from .youtube_oauth import get_valid_access_token

        get_valid_access_token()
        youtube_ready = True
    except Exception:
        youtube_ready = False
    instagram_token = get_platform_secret("instagram") or settings.instagram_access_token
    roles: dict[str, str] = {}
    paid_blocked = True
    cooldowns: dict[str, str | None] = {}
    try:
        from .provider_credentials import (
            get_provider_for_role,
            no_paid_providers_enabled,
            provider_cooldown_until,
        )

        for role in ("script", "voice", "image", "video"):
            try:
                roles[role] = get_provider_for_role(role)
            except Exception:
                roles[role] = "unknown"
        paid_blocked = bool(no_paid_providers_enabled())
        for provider in ("minimax", "gemini", "huggingface", "openai", "elevenlabs", "fish_audio"):
            try:
                until = provider_cooldown_until(provider)
            except Exception:
                until = None
            if until is not None:
                cooldowns[provider] = until.isoformat()
    except Exception:
        pass
    script_credential = False
    script_key_verified = False
    try:
        from .scriptwriter import _cached_minimax_key, minimax_text_candidates

        script_credential = bool(minimax_text_candidates())
        script_key_verified = bool(_cached_minimax_key())
    except Exception:
        script_credential = False
    higgsfield_credential = False
    try:
        from .provider_credentials import provider_status

        higgsfield_credential = bool(provider_status().get("higgsfield"))
    except Exception:
        higgsfield_credential = False
    return {
        "script_model": settings.minimax_model if settings.minimax_api_key else "unconfigured",
        "media_provider": settings.media_provider if settings.media_generation_enabled else "off",
        "google_media": bool(settings.google_media_enabled and settings.google_cloud_project),
        "roles": roles,
        "paid_providers_blocked": paid_blocked,
        "cooldowns": cooldowns,
        "script_credential": script_credential,
        "script_key_verified": script_key_verified,
        "higgsfield_credential": higgsfield_credential,
        "youtube": {
            "enabled": bool(settings.youtube_enabled),
            "credential": youtube_ready,
            "privacy": settings.youtube_privacy_status,
        },
        "instagram": {
            "enabled": bool(settings.instagram_enabled),
            "credential": bool(instagram_token and settings.instagram_user_id),
        },
    }


def _credit_summary_safe() -> dict[str, object]:
    """The Higgsfield money vault, if the module is available."""
    try:
        from .credits import money_summary

        return money_summary()
    except Exception:
        return {}


def _series_summary() -> dict[str, object]:
    """Story series state: how many are running and what they have spent."""
    try:
        from .cost import series_spend
        from .series import list_series, series_state

        series_list = list_series(limit=10)
    except Exception:
        return {"count": 0, "active": []}
    active = []
    spent = 0.0
    for series in series_list:
        state = series_state(series.id)
        try:
            used = series_spend(series.id)
        except Exception:
            used = 0.0
        spent += used
        active.append(
            {
                "id": series.id[:8],
                "topic": series.topic,
                "status": series.status,
                "parts": state["part_count"],
                "published": sum(1 for part in state["parts"] if part["published"]),
                "cadence": series.cadence,
                "spent_usd": used,
                "budget_usd": series.budget_usd,
            }
        )
    return {"count": len(series_list), "spent_usd": round(spent, 4), "active": active}


def pipeline_health() -> dict[str, object]:
    """Return a JSON-serialisable health snapshot of the whole pipeline."""
    from .automation import automation_enabled
    from .cost import spend_summary
    from .hq import (
        get_daily_publish_limit,
        is_auto_mode_enabled,
        is_auto_publish_enabled,
        is_mock_publishing,
    )
    from .services import is_paused, worker_status

    settings = get_settings()
    queues = _queue_depth()
    published_today, failed_publications = _publications_today()
    limit = get_daily_publish_limit()
    workers = worker_status()
    online_workers = [item for item in workers if item.get("online")]
    spend = spend_summary(1)
    qc = _qc_summary()
    active = sum(queues.get(status, 0) for status in ACTIVE_STATUSES)
    paused = is_paused()

    issues: list[str] = []
    if paused:
        issues.append("factory_paused")
    if active and not online_workers:
        issues.append("no_worker_online")
    if queues.get("failed", 0):
        issues.append("failed_jobs_waiting")
    if published_today + failed_publications >= limit:
        issues.append("daily_limit_reached")
    if qc["measured"] and qc["passed"] < qc["measured"]:
        issues.append("media_qc_failures")
    providers = _provider_status()
    if not providers["youtube"]["credential"] and providers["youtube"]["enabled"]:
        issues.append("youtube_credential_invalid")
    if not providers["instagram"]["credential"] and providers["instagram"]["enabled"]:
        issues.append("instagram_credential_invalid")
    if providers.get("paid_providers_blocked") and providers.get("roles", {}).get("script") not in {
        "template",
        None,
    }:
        issues.append("paid_providers_blocked_for_script")
    if (
        providers.get("roles", {}).get("script") == "minimax"
        and not providers.get("script_credential")
    ):
        issues.append("script_provider_credential_missing")

    status = "paused" if paused else ("degraded" if issues else "healthy")
    return {
        "status": status,
        "issues": issues,
        "paused": paused,
        "mock_publishing": is_mock_publishing(),
        "workers": {"online": len(online_workers), "total": len(workers), "detail": workers},
        "queues": queues,
        "active_jobs": active,
        "publications": {
            "published_today": published_today,
            "failed_today": failed_publications,
            "daily_limit": limit,
            "remaining_today": max(0, limit - published_today),
        },
        "spend": {
            "today_usd": spend.get("total_usd", 0.0),
            "by_provider": spend.get("by_provider", {}),
        },
        "providers": providers,
        "automation": {
            "trend_automation": bool(automation_enabled()),
            "auto_mode": bool(is_auto_mode_enabled()),
            "auto_publish": bool(is_auto_publish_enabled()),
            "interval_seconds": settings.automation_interval_seconds,
        },
        "media_qc": qc,
        "series": _series_summary(),
        "credits": _credit_summary_safe(),
        "recent_failures": _recent_failures(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
