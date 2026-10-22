import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from .db import AnalyticsSnapshot, SessionLocal, TrendCandidate


def _bounded(value: int) -> int:
    return max(0, min(100, value))


def score_trend(topic: str, freshness: int, relevance: int, originality: int, risk: int) -> int:
    clean = " ".join(topic.split()).strip()
    if not clean:
        raise ValueError("Trend topic is required")
    return round(
        _bounded(freshness) * 0.30
        + _bounded(relevance) * 0.35
        + _bounded(originality) * 0.25
        + (100 - _bounded(risk)) * 0.10
    )


def add_trend(
    topic: str,
    source: str = "manual",
    source_url: str | None = None,
    freshness: int = 50,
    relevance: int = 50,
    originality: int = 100,
    risk: int = 0,
    transformation_notes: str | None = None,
) -> TrendCandidate:
    if source_url and not re.match(r"^https?://", source_url):
        raise ValueError("Source URL must use HTTPS or HTTP")
    score = score_trend(topic, freshness, relevance, originality, risk)
    with SessionLocal() as session:
        existing = session.scalar(
            select(TrendCandidate).where(
                TrendCandidate.topic == " ".join(topic.split()).strip(),
                TrendCandidate.status != "rejected",
            )
        )
        if existing:
            return existing
        item = TrendCandidate(
            id=str(uuid4()),
            topic=" ".join(topic.split()).strip(),
            source=source[:80],
            source_url=source_url,
            freshness_score=_bounded(freshness),
            relevance_score=_bounded(relevance),
            originality_score=_bounded(originality),
            risk_score=_bounded(risk),
            total_score=score,
            originality_required=75,
            transformation_notes=transformation_notes,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def list_trends(limit: int = 20) -> list[TrendCandidate]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(TrendCandidate)
                .where(TrendCandidate.status == "candidate")
                .order_by(TrendCandidate.total_score.desc(), TrendCandidate.created_at.desc())
                .limit(limit)
            )
        )


def add_analytics_snapshot(
    platform: str,
    account: str,
    content_id: str,
    views: int,
    likes: int,
    comments: int,
    shares: int,
    watch_seconds: int = 0,
) -> AnalyticsSnapshot:
    values = [views, likes, comments, shares, watch_seconds]
    if any(value < 0 for value in values):
        raise ValueError("Analytics values cannot be negative")
    with SessionLocal() as session:
        item = AnalyticsSnapshot(
            platform=platform,
            account=account,
            content_id=content_id,
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            watch_seconds=watch_seconds,
            captured_at=datetime.now(timezone.utc),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


# YouTube Partner Program thresholds (public requirements). Progress below
# is an ESTIMATE from local snapshots, which can double-count and are not
# the official counters — YouTube Studio is the only source of truth.
YPP_SUBSCRIBERS_REQUIRED = 1000
YPP_WATCH_HOURS_REQUIRED = 4000
YPP_SHORTS_VIEWS_REQUIRED = 10_000_000


def monetisation_readiness() -> dict[str, object]:
    """Estimated progress toward YouTube Partner Program thresholds."""
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(AnalyticsSnapshot).where(AnalyticsSnapshot.platform == "youtube")
            )
        )
    total_watch_seconds = sum(row.watch_seconds for row in rows)
    watch_hours = round(total_watch_seconds / 3600, 2)
    total_views = sum(row.views for row in rows)
    return {
        "estimated_watch_hours": watch_hours,
        "watch_hours_required": YPP_WATCH_HOURS_REQUIRED,
        "watch_hours_progress_percent": min(100.0, round(watch_hours / YPP_WATCH_HOURS_REQUIRED * 100, 2)),
        "total_youtube_views": total_views,
        "shorts_views_required": YPP_SHORTS_VIEWS_REQUIRED,
        "subscribers_required": YPP_SUBSCRIBERS_REQUIRED,
        "note": (
            "Estimate from synced snapshots only (may double-count). "
            "Subscriber count and official YPP progress live in YouTube Studio."
        ),
    }


def analytics_recommendations() -> dict[str, object]:
    """Deterministic, honest recommendations computed from snapshots."""
    with SessionLocal() as session:
        rows = list(session.scalars(select(AnalyticsSnapshot)))
    if not rows:
        return {
            "total_snapshots": 0,
            "total_views": 0,
            "average_engagement_rate": 0.0,
            "top_platform": None,
            "top_content_id": None,
            "monetisation": monetisation_readiness(),
            "recommendations": ["No analytics yet — publish and sync analytics first."],
        }
    total_views = sum(row.views for row in rows)
    rates = [
        (row.likes + row.comments + row.shares) / max(row.views, 1) * 100 for row in rows
    ]
    average_rate = round(sum(rates) / len(rates), 2)

    views_by_platform: dict[str, int] = {}
    for row in rows:
        views_by_platform[row.platform] = views_by_platform.get(row.platform, 0) + row.views
    top_platform = max(views_by_platform, key=lambda name: views_by_platform[name])

    # Top content by engagement rate, ignoring near-zero-view snapshots.
    viewed = [row for row in rows if row.views >= 10]
    top_content_id: str | None = None
    if viewed:
        def _rate(row: AnalyticsSnapshot) -> float:
            return (row.likes + row.comments + row.shares) / max(row.views, 1) * 100

        top_content_id = max(viewed, key=_rate).content_id

    recommendations = [
        f"{top_platform.title()} is your top platform by views ({views_by_platform[top_platform]:,} views) — prioritise it when scheduling.",
        f"Average engagement rate is {average_rate}% across {len(rows)} snapshot(s).",
    ]
    if top_content_id:
        recommendations.append(
            f"Your best-performing content is {top_content_id} by engagement rate — study its hook and format before making the next one."
        )
    if average_rate < 2:
        recommendations.append(
            "Test stronger hooks in the first 3 seconds: average engagement is below 2%, so the opening is the first thing to improve."
        )
    # Retention signal: average seconds watched per view (Shorts format).
    total_watch_seconds = sum(row.watch_seconds for row in rows)
    if total_views > 0 and total_watch_seconds > 0:
        avg_view_seconds = total_watch_seconds / total_views
        if avg_view_seconds < 20:
            recommendations.append(
                f"Viewers watch only ~{avg_view_seconds:.0f}s per view on average — "
                "tighten the first 3 seconds and cut any slow scene; retention is the fastest route to watch hours."
            )
        else:
            recommendations.append(
                f"Average view duration is ~{avg_view_seconds:.0f}s per view — retention is healthy; "
                "repeat this format and hook style."
            )
    readiness = monetisation_readiness()
    recommendations.append(
        f"Monetisation progress (estimate): {readiness['estimated_watch_hours']} of "
        f"{YPP_WATCH_HOURS_REQUIRED} public watch hours toward the YouTube Partner Program "
        f"({readiness['watch_hours_progress_percent']}%). Official progress: YouTube Studio."
    )
    return {
        "total_snapshots": len(rows),
        "total_views": total_views,
        "average_engagement_rate": average_rate,
        "top_platform": top_platform,
        "top_content_id": top_content_id,
        "monetisation": readiness,
        "recommendations": recommendations,
    }


def analytics_summary(limit: int = 25) -> list[dict[str, object]]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.captured_at.desc()).limit(limit)
        )
        return [
            {
                "platform": row.platform,
                "account": row.account,
                "content_id": row.content_id,
                "views": row.views,
                "likes": row.likes,
                "comments": row.comments,
                "shares": row.shares,
                "engagement_rate": round((row.likes + row.comments + row.shares) / max(row.views, 1) * 100, 2),
                "watch_seconds": row.watch_seconds,
                "captured_at": row.captured_at.isoformat(),
            }
            for row in rows
        ]
