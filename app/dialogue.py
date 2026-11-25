"""Multi-speaker dialogue.

A cinematic character reel is a conversation, not a monologue: one character
answers, the other reacts. This module turns a tagged script into one audio
track where every line is spoken by its own character, with its own voice, and
the right silence between the lines.
"""
import re
from dataclasses import dataclass

from .characters import Character, get_character

LINE_PATTERN = re.compile(
    r"^\s*[\[\(]?\s*([^:\[\]\(\)]{1,28}?)\s*[\]\)]?\s*:\s*(.+?)\s*$"
)


@dataclass(frozen=True)
class DialogueLine:
    speaker: str
    text: str
    character_id: str


def parse_dialogue(script: str) -> list[DialogueLine]:
    """Read tagged dialogue lines out of a script.

    A tagged line looks like ``शेरू भाई: पचास करोड़? ठीक है।`` or
    ``[शेरू भाई] पचास करोड़? ठीक है।``. Untagged paragraphs are treated as
    narration by the lead character.
    """
    lines: list[DialogueLine] = []
    for raw_line in (script or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = LINE_PATTERN.match(stripped)
        if match:
            speaker = match.group(1).strip()
            text = match.group(2).strip()
            if speaker and text:
                lines.append(
                    DialogueLine(
                        speaker=speaker,
                        text=text,
                        character_id=_match_character(speaker),
                    )
                )
            continue
        if stripped.startswith("-") or stripped.startswith("—"):
            stripped = stripped.lstrip("-— ").strip()
        if len(stripped) >= 3:
            lines.append(
                DialogueLine(
                    speaker="",
                    text=stripped,
                    character_id="",
                )
            )
    return lines


def _match_character(speaker: str) -> str:
    """Map a spoken name to a character id."""
    from .characters import CHARACTERS

    needle = speaker.strip().casefold()
    for character in CHARACTERS.values():
        if needle == character.character_id.casefold():
            return character.character_id
        if needle == character.label.casefold():
            return character.character_id
        if character.label.casefold() in needle or needle in character.label.casefold():
            return character.character_id
    for character in CHARACTERS.values():
        if character.character_id.replace("_", " ") in needle:
            return character.character_id
    return ""


def cast_for_lines(lines: list[DialogueLine]) -> list[Character]:
    """The characters that speak in a script, lead character first."""
    seen: list[str] = []
    for line in lines:
        character_id = line.character_id or ""
        if character_id and character_id not in seen:
            seen.append(character_id)
    if not seen:
        return []
    return [get_character(character_id) for character_id in seen]


def dialogue_text(lines: list[DialogueLine]) -> str:
    """The spoken words only, without the speaker tags."""
    return " ".join(line.text for line in lines if line.text).strip()


def script_for_captions(lines: list[DialogueLine]) -> str:
    """The script as the caption engine should read it, one line per turn."""
    return "\n".join(
        f"{line.speaker}: {line.text}" if line.speaker else line.text
        for line in lines
        if line.text
    )
