from app.characters import (
    CHARACTERS,
    DEFAULT_CHARACTER_ID,
    cast_prompt,
    character_list,
    default_character,
    get_character,
)
from app.dialogue import (
    cast_for_lines,
    dialogue_text,
    parse_dialogue,
    script_for_captions,
)


def test_the_roster_has_default_characters_with_distinct_voices():
    assert len(CHARACTERS) >= 5
    assert default_character().character_id == DEFAULT_CHARACTER_ID
    assert default_character().default is True
    voices = {
        character.voice_profile.get("pitch") for character in CHARACTERS.values()
    }
    assert len(voices) >= 3


def test_every_character_has_a_reference_sheet():
    for character in CHARACTERS.values():
        sheet = character.sheet()
        assert "reference sheet" in sheet
        assert character.look[:20] in sheet


def test_unknown_character_falls_back_to_the_default():
    assert get_character("not_a_character").character_id == DEFAULT_CHARACTER_ID


def test_character_list_is_reportable():
    listed = character_list()

    assert len(listed) == len(CHARACTERS)
    assert any(item["default"] for item in listed)


def test_parse_dialogue_reads_tagged_lines():
    script = "शेरू भाई: पचास करोड़? ठीक है।\nभालू भाई: सौ करोड़?!"

    lines = parse_dialogue(script)

    assert [line.text for line in lines] == ["पचास करोड़? ठीक है।", "सौ करोड़?!"]
    assert lines[0].character_id == "emerald_panther"
    assert lines[1].character_id == "bear_mafia"


def test_parse_dialogue_keeps_untagged_narration():
    lines = parse_dialogue("आज का बाज़ार बदल गया।")

    assert len(lines) == 1
    assert lines[0].text == "आज का बाज़ार बदल गया।"


def test_cast_for_lines_lists_the_speakers():
    script = "शेरू भाई: लाखों करोड़?\nबाघ सर: मेरी बारी।"

    cast = cast_for_lines(parse_dialogue(script))

    assert [character.character_id for character in cast] == [
        "emerald_panther",
        "white_tiger_don",
    ]


def test_dialogue_text_drops_the_tags():
    script = "शेरू भाई: पचास करोड़?\nभालू भाई: सौ करोड़!"

    assert dialogue_text(parse_dialogue(script)) == "पचास करोड़? सौ करोड़!"
    assert "शेरू भाई:" in script_for_captions(parse_dialogue(script))


def test_cast_prompt_lists_every_character():
    prompt = cast_prompt([default_character()])

    assert "emerald_panther" in prompt
    assert default_character().personality[:20] in prompt
