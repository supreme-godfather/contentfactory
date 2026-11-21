from typing import Sequence


def character_alignment_to_words(
    characters: Sequence[str],
    starts: Sequence[float],
    ends: Sequence[float],
) -> list[dict[str, object]]:
    if not (len(characters) == len(starts) == len(ends)):
        raise ValueError("Voice alignment arrays must have the same length.")
    words: list[dict[str, object]] = []
    current: list[str] = []
    start = 0.0
    end = 0.0
    for character, raw_start, raw_end in zip(characters, starts, ends, strict=True):
        if not isinstance(character, str):
            raise ValueError("Voice alignment contains an invalid character.")
        try:
            item_start = max(0.0, float(raw_start))
            item_end = max(item_start, float(raw_end))
        except (TypeError, ValueError) as exc:
            raise ValueError("Voice alignment contains an invalid timestamp.") from exc
        if character.isspace():
            if current:
                words.append({
                    "word": "".join(current),
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                })
                current = []
            continue
        if not current:
            start = item_start
        current.append(character)
        end = item_end
    if current:
        words.append({
            "word": "".join(current),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
        })
    return words


def words_to_caption_cues(
    words: Sequence[dict[str, object]],
    *,
    max_words: int = 4,
    max_chars: int = 38,
) -> list[dict[str, object]]:
    cues: list[dict[str, object]] = []
    current: list[dict[str, object]] = []
    for word in words:
        text = str(word.get("word") or "").strip()
        if not text:
            continue
        candidate = " ".join([str(item["word"]) for item in current] + [text])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            cues.append(_cue(len(cues) + 1, current))
            current = []
        current.append({**word, "word": text})
    if current:
        cues.append(_cue(len(cues) + 1, current))
    return cues


def _cue(index: int, words: Sequence[dict[str, object]]) -> dict[str, object]:
    return {
        "index": index,
        "text": " ".join(str(item["word"]) for item in words),
        "start_seconds": float(words[0]["start_seconds"]),
        "end_seconds": float(words[-1]["end_seconds"]),
        "words": list(words),
        "timing_source": "provider_alignment",
    }
