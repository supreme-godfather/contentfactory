"""Performance lessons that feed the next script.

A media engine that cannot learn from its own output is just a generator. After
analytics are synced the engine distils what worked and what did not into a
short set of directives, and the writer sees them on the next job.
"""
from sqlalchemy import select

from .db import AnalyticsSnapshot, SessionLocal

MIN_SAMPLES = 1


def _averages(rows: list[AnalyticsSnapshot]) -> dict[str, float]:
    if not rows:
        return {}
    views = sum(row.views for row in rows)
    watch_seconds = sum(row.watch_seconds for row in rows)
    engagement = [
        (row.likes + row.comments + row.shares) / max(row.views, 1) * 100 for row in rows
    ]
    return {
        "views": views,
        "average_view_seconds": watch_seconds / views if views else 0.0,
        "average_engagement_rate": sum(engagement) / len(engagement),
        "samples": len(rows),
    }


def performance_lessons(limit: int = 4) -> list[str]:
    """Short directives derived from synced analytics."""
    with SessionLocal() as session:
        rows = list(
            session.scalars(
                select(AnalyticsSnapshot).order_by(AnalyticsSnapshot.captured_at.desc())
            )
        )
    if not rows:
        return []
    stats = _averages(rows)
    lessons: list[str] = []

    average_view_seconds = stats["average_view_seconds"]
    if average_view_seconds > 0:
        if average_view_seconds < 12:
            lessons.append(
                "Viewers leave before 12 seconds: put the payoff, the number or the "
                "surprise in the very first line and cut every warm-up sentence."
            )
        elif average_view_seconds < 25:
            lessons.append(
                "Viewers watch roughly 20 seconds: make every sentence before the "
                "twenty second mark earn the next one."
            )
        else:
            lessons.append(
                "Retention is healthy — keep the same pacing and structure that is "
                "already holding viewers past the first line."
            )

    engagement = stats["average_engagement_rate"]
    if engagement and engagement < 2:
        lessons.append(
            "Engagement is under 2%: end with a question the viewer can answer in "
            "one word and state the promise of the video in the first five seconds."
        )

    best = max(
        rows,
        key=lambda row: (row.likes + row.comments + row.shares) / max(row.views, 1),
        default=None,
    )
    if best is not None and best.views >= 10:
        lessons.append(
            f"Your strongest video so far is '{best.content_id}' "
            f"({best.views} views) — study its opening line and mirror that structure."
        )

    platform_totals: dict[str, int] = {}
    for row in rows:
        platform_totals[row.platform] = platform_totals.get(row.platform, 0) + row.views
    if platform_totals:
        top_platform = max(platform_totals, key=lambda name: platform_totals[name])
        lessons.append(
            f"{top_platform.title()} brings the most views on this channel — write for it first."
        )

    return lessons[:limit]


def lessons_block(limit: int = 4) -> str:
    """The lessons rendered as a prompt block (empty when there is no data)."""
    try:
        lessons = performance_lessons(limit)
    except Exception:
        return ""
    if not lessons:
        return ""
    lines = "\n".join(f"- {lesson}" for lesson in lessons)
    return f"\nLessons from your previous videos (apply them):\n{lines}\n"
