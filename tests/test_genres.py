import pytest


def test_genre_library_is_complete_and_unique():
    from app.genres import GENRES, GENRE_BY_ID, genre_directives

    assert len(GENRES) == 48
    assert len(GENRE_BY_ID) == 48
    for genre in GENRES:
        assert genre["id"]
        assert genre["label"]
        assert genre["category"]
        assert genre["engine"]
        assert genre["hook"]
        assert genre["asset"] in {
            "ai_video",
            "still_kenburns",
            "stickman_code",
            "gameplay_zoom",
            "manim_chart",
        }
    directives = genre_directives(GENRES[0])
    assert "Visual engine" in directives
    assert "Hook style" in directives


def test_genre_modes_choose_specific_shuffle_random_and_auto():
    from app.genres import match_genre_for_topic, random_genre
    from app.hq import set_genre_id, set_genre_mode

    set_genre_mode("specific")
    set_genre_id("hq_data_race")
    from app.auto_flow import resolve_genre

    genre, reason = resolve_genre("कोई भी विषय")
    assert genre["id"] == "hq_data_race"
    assert "owner selected" in reason

    set_genre_mode("shuffle")
    genre, reason = resolve_genre("कोई भी विषय")
    assert genre["id"]
    assert "shuffled" in reason

    set_genre_mode("random")
    genre, reason = resolve_genre("कोई भी विषय")
    assert genre["id"]
    assert reason == "random genre"

    set_genre_mode("auto")
    genre, reason = resolve_genre("UPI payment network data race chart")
    assert genre["category"] in {"motion", "business", "facts"}
    assert reason == "matched to topic"


def test_topic_matching_is_deterministic():
    from app.genres import match_genre_for_topic

    first = match_genre_for_topic("gta6 physics glitch explained")
    second = match_genre_for_topic("gta6 physics glitch explained")
    assert first["id"] == second["id"]
    assert first["category"] == "gaming"


def test_random_genre_stays_inside_the_category():
    from app.genres import random_genre

    for _ in range(5):
        assert random_genre("facts")["category"] == "facts"


def test_auto_mode_settings_round_trip():
    from app.hq import (
        get_auto_publish_max_cost_usd,
        is_auto_mode_enabled,
        is_auto_publish_enabled,
        set_auto_mode_enabled,
        set_auto_publish_enabled,
        set_auto_publish_max_cost_usd,
    )

    assert is_auto_mode_enabled() is False
    set_auto_mode_enabled(True)
    assert is_auto_mode_enabled() is True
    set_auto_publish_enabled(True)
    assert is_auto_publish_enabled() is True
    assert set_auto_publish_max_cost_usd(1.5) == 1.5
    assert get_auto_publish_max_cost_usd() == 1.5
    assert set_auto_publish_max_cost_usd(99) == 25.0
    set_auto_mode_enabled(False)
    set_auto_publish_enabled(False)


def test_auto_mode_approves_and_generates_without_owner_action():
    from unittest.mock import patch
    from app.hq import set_auto_mode_enabled
    from app.services import create_job, generate_content

    set_auto_mode_enabled(True)
    job = create_job("ऑटोमैटिक परीक्षण", "youtube", request_id="auto-mode")
    with (
        patch("app.services.build_script_package", return_value={
            "script": "यह एक स्वतः स्वीकृत स्क्रिप्ट है जो काफी लंबा है। " * 8,
            "metadata": {},
            "critic": {"applied": False},
        }),
        patch("app.services.create_job_assets", return_value={"voiceover": "v.mp3"}),
        patch("app.provider_credentials.get_provider_for_role", return_value="off"),
        patch("app.provider_credentials.media_generation_enabled", return_value=False),
    ):
        result = generate_content(job.id)

    assert result.status == "queued"
    assert result.stage == "render"
    set_auto_mode_enabled(False)


def test_auto_activity_log_records_only_safe_lines():
    from app.auto_flow import recent_automation_events, record_automation_event

    record_automation_event("auto_published", "job-123", "platform=youtube")
    events = recent_automation_events(5)
    assert events
    assert "auto_published" in events[-1]
    assert "job-123" in events[-1]
