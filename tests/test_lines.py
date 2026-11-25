from app.lines import (
    CATEGORIES,
    MONEY_GRAMMAR,
    PUNCH_RULES,
    register_directives,
    signature_line_for,
)


def test_the_library_covers_the_four_scene_families():
    keys = {category.key for category in CATEGORIES}

    assert {"boss_entry", "attitude", "street", "family", "rival"} <= keys
    assert all(category.lines for category in CATEGORIES)


def test_every_line_is_short_and_spoken():
    for category in CATEGORIES:
        for line in category.lines:
            assert 3 <= len(line.split()) <= 10


def test_the_register_directives_carry_the_examples():
    directives = register_directives()

    assert "The boss arrives" in directives
    assert "पचास करोड़" in directives
    assert MONEY_GRAMMAR.split(":")[0][:20] in directives


def test_the_punch_rules_forbid_the_explainer_traps():
    assert "no subscribe line" in PUNCH_RULES.lower()
    assert "quotable" in PUNCH_RULES.lower()


def test_a_signature_line_is_chosen_for_the_speaker():
    boss = signature_line_for("शेरू भाई")
    rival = signature_line_for("ग्रीम पैंथर")

    assert boss and rival
    assert boss != rival


def test_an_unknown_speaker_still_gets_a_line():
    assert signature_line_for("कोई और")
