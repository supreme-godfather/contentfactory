"""Provider cost tracking, estimation and enforcement.

The factory spends real money on every video, so the cost of each generation
step is written to a ledger, estimated before a job spends anything, and
checked against the owner's per-video ceiling. Prices live in settings so a
price change never needs a code release.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import FactorySetting, ProviderUsage, SessionLocal

DEFAULT_UNIT_PRICES: dict[str, float] = {
    "minimax_video_per_second": 0.06,
    "minimax_image": 0.022,
    "minimax_llm_per_1k_tokens": 0.0006,
    "minimax_speech_per_1k_chars": 0.06,
    "elevenlabs_per_1k_chars": 0.04,
    "fish_tts_per_1k_chars": 0.05,
    "higgsfield_video_per_second": 0.084,
    "google_image": 0.02,
    "google_speech_per_1k_chars": 0.016,
    "openai_image": 0.04,
    "openai_speech_per_1k_chars": 0.015,
    "openai_llm_per_1k_tokens": 0.0003,
    "gemini_llm_per_1k_tokens": 0.0002,
}


def get_unit_prices() -> dict[str, float]:
    """Configured unit prices, falling back to the built-in defaults."""
    prices = dict(DEFAULT_UNIT_PRICES)
    try:
        with SessionLocal() as session:
            setting = session.get(FactorySetting, "unit_prices")
    except Exception:
        return prices
    if not setting or not setting.value:
        return prices
    try:
        stored = json.loads(setting.value)
    except json.JSONDecodeError:
        return prices
    if isinstance(stored, dict):
        for key, value in stored.items():
            try:
                prices[str(key)] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return prices


def record_usage(
    job_id: str,
    provider: str,
    kind: str,
    units: float,
    unit: str,
    unit_price_usd: float,
) -> float:
    """Write one provider spend line and return its cost in US dollars."""
    cost = round(max(0.0, units) * max(0.0, unit_price_usd), 6)
    with SessionLocal() as session:
        session.add(ProviderUsage(
            job_id=job_id,
            provider=provider,
            kind=kind,
            units=round(max(0.0, units), 4),
            unit=unit,
            unit_price_usd=unit_price_usd,
            cost_usd=cost,
        ))
        session.commit()
    return cost


def job_cost(job_id: str) -> float:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(ProviderUsage.cost_usd), 0.0)).where(
                ProviderUsage.job_id == job_id
            )
        )
    return round(float(total or 0.0), 4)


def estimate_job_cost(metadata: dict[str, object], script_text: str) -> dict[str, float]:
    """Estimate the provider spend for one video from its planned assets."""
    prices = get_unit_prices()
    script_chars = max(0, len(script_text or ""))
    scene_count = 6
    video_scenes = 2
    image_scenes = max(0, scene_count - video_scenes)
    seconds_per_clip = 5
    video_provider = str(metadata.get("video_provider") or "off")
    image_provider = str(metadata.get("image_provider") or "off")
    voice_provider = str(metadata.get("voice_provider") or "off")
    breakdown: dict[str, float] = {}

    if video_provider == "minimax":
        breakdown["minimax_video"] = round(
            video_scenes * seconds_per_clip * prices["minimax_video_per_second"], 4
        )
    elif video_provider == "higgsfield":
        breakdown["higgsfield_video"] = round(
            video_scenes * seconds_per_clip * prices["higgsfield_video_per_second"], 4
        )
    if image_provider == "minimax":
        breakdown["minimax_image"] = round(image_scenes * prices["minimax_image"], 4)
    elif image_provider == "google":
        breakdown["google_image"] = round(image_scenes * prices["google_image"], 4)
    elif image_provider == "openai":
        breakdown["openai_image"] = round(image_scenes * prices["openai_image"], 4)
    if voice_provider == "minimax":
        breakdown["minimax_speech"] = round(
            script_chars / 1000 * prices["minimax_speech_per_1k_chars"], 4
        )
    elif voice_provider == "elevenlabs":
        breakdown["elevenlabs_speech"] = round(
            script_chars / 1000 * prices["elevenlabs_per_1k_chars"], 4
        )
    elif voice_provider == "fish_audio":
        breakdown["fish_tts"] = round(script_chars / 1000 * prices["fish_tts_per_1k_chars"], 4)
    elif voice_provider == "google":
        breakdown["google_speech"] = round(
            script_chars / 1000 * prices["google_speech_per_1k_chars"], 4
        )
    breakdown["script_llm"] = round(
        (script_chars / 4 / 1000) * prices["minimax_llm_per_1k_tokens"] * 3, 4
    )
    breakdown["total"] = round(sum(breakdown.values()), 4)
    return breakdown


def enforce_cost_ceiling(job_id: str, metadata: dict[str, object], script_text: str) -> dict[str, float]:
    """Stop a job that would exceed the owner's per-video spending ceiling."""
    from .hq import get_auto_publish_max_cost_usd

    estimate = estimate_job_cost(metadata, script_text)
    spent = job_cost(job_id)
    ceiling = get_auto_publish_max_cost_usd()
    projected = round(spent + estimate["total"], 4)
    if projected > ceiling:
        from .auto_flow import record_automation_event

        record_automation_event(
            "cost_ceiling_blocked",
            job_id,
            f"projected=${projected:.2f} ceiling=${ceiling:.2f}",
        )
        raise RuntimeError(
            f"Cost ceiling reached: this video would cost about ${projected:.2f} but the "
            f"per-video limit is ${ceiling:.2f}. Raise the limit or pick cheaper providers."
        )
    return estimate


def spend_summary(days: int = 30) -> dict[str, object]:
    """Total and per-provider spend for the recent window."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(ProviderUsage).where(ProviderUsage.created_at >= since)
            )
        )
        job_totals = dict(
            session.execute(
                select(ProviderUsage.job_id, func.coalesce(func.sum(ProviderUsage.cost_usd), 0.0))
                .where(ProviderUsage.created_at >= since)
                .group_by(ProviderUsage.job_id)
            ).all()
        )
    by_provider: dict[str, float] = {}
    for row in rows:
        by_provider[row.provider] = round(
            by_provider.get(row.provider, 0.0) + float(row.cost_usd or 0.0), 4
        )
    return {
        "window_days": days,
        "total_usd": round(sum(by_provider.values()), 4),
        "by_provider": by_provider,
        "jobs": len(job_totals),
        "most_expensive_job": round(max(job_totals.values()), 4) if job_totals else 0.0,
    }
