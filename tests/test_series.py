import json

from app.db import ContentJob, SessionLocal, init_db


def _plan(part_count: int = 6):
    from app.series import PartPlan, SeriesPlan

    return SeriesPlan(
        topic="Test story",
        pack_id="cinematic_boss_series",
        part_count=part_count,
        logline="A boss walks into the city.",
        character="A calm panther boss in an emerald suit.",
        setting="A city at midnight.",
        parts=tuple(
            PartPlan(
                index=index,
                title=f"भाग {index}",
                beats=("entrance", "challenge", "escalation", "cliffhanger"),
                cliffhanger=f"लेकिन भाग {index} का सच तब सामने आया…",
                teaser=f"भाग {index + 1} में।" if index < part_count else "",
            )
            for index in range(1, part_count + 1)
        ),
    )


def test_pack_registry_has_the_series_packs():
    from app.packs import PACKS, SERIES_PACKS

    assert "cinematic_boss_series" in SERIES_PACKS
    assert "desi_boss_monologue" in SERIES_PACKS
    assert PACKS["cinematic_boss_series"].needs_character_image is True
    assert PACKS["desi_boss_monologue"].needs_character_image is False


def test_pack_directives_include_the_beat_map():
    from app.packs import get_pack, pack_directives

    directives = pack_directives(get_pack("cinematic_boss_series"))

    assert "cliffhanger" in directives
    assert "Length" in directives


def test_legacy_niches_map_to_packs():
    from app.hq import get_active_pack_id, set_niche

    set_niche("hindi_money")

    assert get_active_pack_id() == "hindi_money"


def test_fallback_plan_always_has_every_part():
    from app.packs import get_pack
    from app.series import _fallback_plan

    plan = _fallback_plan("कहानी", get_pack("desi_boss_monologue"), 6)

    assert len(plan.parts) == 6
    assert plan.parts[0].cliffhanger
    assert plan.parts[-1].teaser == ""


def test_create_series_makes_one_job_per_part(monkeypatch):
    from app import series as series_module

    monkeypatch.setattr(series_module, "plan_series", lambda *args, **kwargs: _plan())
    series_module.create_series("Test story", "cinematic_boss_series", part_count=6)

    with SessionLocal() as session:
        jobs = list(session.query(ContentJob).all())
    assert len(jobs) == 6
    statuses = sorted(job.status for job in jobs)
    assert statuses.count("queued") == 1
    assert statuses.count("held") == 5
    for job in jobs:
        metadata = json.loads(job.metadata_json)
        assert metadata["part_count"] == 6
        assert metadata["cliffhanger"]


def test_series_state_lists_every_part(monkeypatch):
    from app import series as series_module

    monkeypatch.setattr(series_module, "plan_series", lambda *args, **kwargs: _plan())
    series, jobs = series_module.create_series(
        "Test story", "cinematic_boss_series", part_count=6
    )

    state = series_module.series_state(series.id)

    assert len(state["parts"]) == 6
    assert state["pack_label"]
    assert state["cadence"] == "daily"


def test_publish_order_blocks_a_later_part(monkeypatch):
    from app import series as series_module

    monkeypatch.setattr(series_module, "plan_series", lambda *args, **kwargs: _plan())
    series, jobs = series_module.create_series(
        "Test story", "cinematic_boss_series", part_count=6
    )
    later = jobs[3]

    assert series_module.publish_order_ok(later) is False

    with SessionLocal() as session:
        for earlier_job in jobs[:3]:
            earlier = session.get(ContentJob, earlier_job.id)
            earlier.status = "published"
        session.commit()

    assert series_module.publish_order_ok(later) is True


def test_release_next_part_unlocks_the_following_part(monkeypatch):
    from app import series as series_module

    monkeypatch.setattr(series_module, "plan_series", lambda *args, **kwargs: _plan())
    series, jobs = series_module.create_series(
        "Test story", "cinematic_boss_series", part_count=6
    )
    with SessionLocal() as session:
        first = session.get(ContentJob, jobs[0].id)
        first.status = "published"
        session.commit()

    released = series_module.release_next_part(series.id)

    assert released is not None
    with SessionLocal() as session:
        second = session.get(ContentJob, jobs[1].id)
    assert second.status == "queued"
    assert second.scheduled_at is not None


def test_series_budget_blocks_more_spend(monkeypatch):
    from app import series as series_module
    from app.cost import enforce_series_budget, record_usage, series_spend

    monkeypatch.setattr(series_module, "plan_series", lambda *args, **kwargs: _plan())
    series, jobs = series_module.create_series(
        "Test story", "cinematic_boss_series", part_count=6, budget_usd=0.5
    )
    record_usage(jobs[0].id, "minimax", "video", 5, "second", 0.1)

    assert series_spend(series.id) == 0.5

    try:
        enforce_series_budget(series.id)
    except ValueError as exc:
        assert "budget" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected the budget to be enforced")


def test_quality_gate_requires_the_cliffhanger():
    from app.quality import validate_content

    metadata = {
        "title": "भाग 1",
        "description": "Description",
        "series_id": "series-1",
        "part_index": 1,
        "part_count": 6,
        "cliffhanger": "लेकिन असली खेल अभी शुरू हुआ।",
    }
    report = validate_content(
        topic="कहानी",
        script="पहला वाक्य।\n\nदूसरा वाक्य।",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        render_path="media/video.mp4",
    )

    assert "missing_cliffhanger" in report.blockers

    metadata["script"] = None
    good = validate_content(
        topic="कहानी",
        script="पहला वाक्य।\n\nलेकिन असली खेल अभी शुरू हुआ।",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        render_path="media/video.mp4",
    )

    assert "missing_cliffhanger" not in good.blockers


def test_cliffhanger_is_restored_after_the_editor_pass():
    from app.scriptwriter import _enforce_cliffhanger

    script = _enforce_cliffhanger(
        "कहानी का अंत।",
        {"cliffhanger": "लेकिन असली खेल अभी शुरू हुआ।"},
    )

    assert script.endswith("लेकिन असली खेल अभी शुरू हुआ।")
