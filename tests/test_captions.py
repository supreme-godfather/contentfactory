import pytest


def test_character_alignment_becomes_semantic_caption_cues():
    from app.captions import character_alignment_to_words, words_to_caption_cues

    text = "यह एक सही परीक्षण है"
    characters = list(text)
    starts = [index * 0.08 for index in range(len(characters))]
    ends = [(index + 1) * 0.08 for index in range(len(characters))]
    words = character_alignment_to_words(characters, starts, ends)
    cues = words_to_caption_cues(words, max_words=3, max_chars=20)

    assert [word["word"] for word in words] == ["यह", "एक", "सही", "परीक्षण", "है"]
    assert cues[0]["text"] == "यह एक सही"
    assert cues[0]["timing_source"] == "provider_alignment"
    assert cues[-1]["end_seconds"] == words[-1]["end_seconds"]


def test_character_alignment_rejects_mismatched_arrays():
    from app.captions import character_alignment_to_words

    with pytest.raises(ValueError, match="same length"):
        character_alignment_to_words(["a"], [0.0], [])
