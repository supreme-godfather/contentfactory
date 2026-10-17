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
