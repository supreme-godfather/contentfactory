from app.scene_templates import (
    TEMPLATES,
    plan_scene_templates,
    template_directive,
    template_for,
    templates_for_pack,
)


def test_the_template_library_covers_the_proven_scenes():
    ids = {template.template_id for template in TEMPLATES}

    assert "boss_entry" in ids
    assert "auction" in ids
    assert "street_chai" in ids
    assert len(TEMPLATES) >= 12


def test_template_for_returns_a_template():
    template = template_for("boss_entry")

    assert template is not None
    assert template.camera
    assert template.beats


def test_template_for_unknown_returns_none():
    assert template_for("not_a_scene") is None


def test_a_pack_gets_its_own_vocabulary():
    boss = templates_for_pack("cinematic_boss_series")
    monologue = templates_for_pack("desi_boss_monologue")

    assert len(boss) > len(monologue)
    assert all(template.template_id in {item.template_id for item in boss} for template in monologue)


def test_planning_a_series_gives_every_part_a_template():
    plan = plan_scene_templates("शेरू भाई का दुश्मन", "cinematic_boss_series", 6)

    assert len(plan) == 6
    assert all(template.template_id for template in plan)


def test_planning_is_deterministic():
    first = plan_scene_templates("कहानी", "cinematic_boss_series", 6)
    second = plan_scene_templates("कहानी", "cinematic_boss_series", 6)

    assert [item.template_id for item in first] == [item.template_id for item in second]


def test_template_directive_carries_the_scene_direction():
    directive = template_directive(template_for("auction"))

    assert "Scene:" in directive
    assert "Camera:" in directive
    assert "Beats:" in directive
