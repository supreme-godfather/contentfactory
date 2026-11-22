from app.db import AnalyticsSnapshot, SessionLocal, init_db


def _snapshot(**overrides):
    init_db()
    values = {
        "platform": "youtube",
        "account": "channel",
        "content_id": "video-1",
        "views": 100,
        "likes": 2,
        "comments": 0,
        "shares": 1,
        "watch_seconds": 1000,
    }
    values.update(overrides)
    with SessionLocal() as session:
        session.add(AnalyticsSnapshot(**values))
        session.commit()


def test_no_lessons_without_analytics():
    from app.lessons import performance_lessons

    init_db()

    assert performance_lessons() == []


def test_short_view_duration_teaches_a_faster_hook():
    from app.lessons import performance_lessons

    _snapshot(views=100, watch_seconds=800, likes=0, comments=0, shares=0)

    lessons = performance_lessons()

    assert lessons
    assert any("first line" in lesson for lesson in lessons)


def test_healthy_retention_teaches_pacing():
    from app.lessons import performance_lessons

    _snapshot(views=100, watch_seconds=4000, likes=10, comments=2, shares=1)

    lessons = performance_lessons()

    assert any("Retention is healthy" in lesson for lesson in lessons)


def test_lessons_block_is_empty_without_data():
    from app.lessons import lessons_block

    init_db()

    assert lessons_block() == ""


def test_lessons_block_lists_directives():
    from app.lessons import lessons_block

    _snapshot(views=100, watch_seconds=500, likes=1, comments=0, shares=0)

    block = lessons_block()

    assert "Lessons from your previous videos" in block
    assert "- " in block
