"""The default character roster.

Every character in the roster is original: a distinct look, a distinct
personality, and a distinct voice. The voice is built from the single
configured TTS voice by shifting speed and pitch, so a scene with two
characters actually sounds like two people talking.

A character is data, not code: a reference sheet prompt (the same character in
every shot), a voice profile, and the way it speaks.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Character:
    character_id: str
    label: str
    role: str
    look: str
    personality: str
    voice_profile: dict[str, float] = field(default_factory=dict)
    speech: str = ""
    sheet_prompt: str = ""
    default: bool = False

    def sheet(self) -> str:
        return (
            "A photorealistic character reference sheet of "
            f"{self.look}. "
            "Two rows on a plain light-grey studio background: three "
            "head-and-shoulders portraits (front, three-quarter, right profile) on top, "
            "four full-body views (front, three-quarter, right profile, back) below. "
            "The character must look identical in all seven views. "
            "Realistic fur and fabric, natural proportions, even lighting, sharp detail. "
            "No text, labels, captions or watermarks."
        )


CHARACTERS: dict[str, Character] = {
    "white_lion_boss": Character(
        character_id="white_lion_boss",
        label="शेरू भाई",
        role="The white-suited lion boss every story starts with",
        look=(
            "a photoreal adult white lion with a full white mane, a human-like upright "
            "body, calm confident amber eyes and a faint authoritative smirk. He wears a "
            "white three-piece suit, a black tie, a thick gold chain, a gold watch and "
            "black sunglasses"
        ),
        personality="calm, unbothered, speaks once and the room obeys",
        voice_profile={"speed": 0.9, "pitch": -2, "vol": 1},
        speech="Short, heavy, confident Hindi lines. Never threatens, never curses.",
        default=True,
    ),
    "emerald_panther": Character(
        character_id="emerald_panther",
        label="ग्रीम पैंथर",
        role="The rival who answers every bid",
        look=(
            "an original anthropomorphic black panther businessman with a human-like body, "
            "sleek black fur, a thick mane, expressive amber eyes and a calm, faintly amused "
            "expression. He wears a deep emerald velvet three-piece suit, a black tie, dark "
            "sunglasses, a gold watch and a heavy gold chain"
        ),
        personality="calm, unbothered, answers with one absurd number",
        voice_profile={"speed": 0.92, "pitch": -1, "vol": 1},
        speech="Short, heavy, confident Hindi lines. Never threatens, never curses.",
    ),
    "bear_mafia": Character(
        character_id="bear_mafia",
        label="भालू भाई",
        role="The loud rival who always overplays his bid",
        look=(
            "an original anthropomorphic brown bear mafia boss with a human-like body, thick "
            "dark-chestnut brown fur, a broad face and an easily offended expression. He wears "
            "a black pinstripe double-breasted suit, a crimson silk shirt, a thick gold chain "
            "and polished black shoes"
        ),
        personality="loud, competitive, always tries to prove himself",
        voice_profile={"speed": 1.05, "pitch": 1, "vol": 1},
        speech="Loud, competitive Hindi. He always talks a little too much.",
    ),
    "snow_leopard_king": Character(
        character_id="snow_leopard_king",
        label="बर्फ़ीली बादशाह",
        role="The old money rival, quietly richer",
        look=(
            "an original anthropomorphic snow leopard tycoon with a human-like body, pale "
            "silver-grey fur with dark rosette spots, a thick cream chest ruff and narrow "
            "calm eyes. He wears a midnight-blue velvet three-piece suit with a silver pocket "
            "square and a silver wristwatch"
        ),
        personality="polished, patient, never raises his voice",
        voice_profile={"speed": 0.93, "pitch": -1, "vol": 1},
        speech="Measured, old-money Hindi. Short sentences that land like a verdict.",
    ),
    "white_tiger_don": Character(
        character_id="white_tiger_don",
        label="बाघ सर",
        role="The stylish fixer who makes it happen",
        look=(
            "an original anthropomorphic white tiger with a human-like body, bright white fur "
            "with black stripes, sharp green eyes and a confident smirk. He wears an ivory "
            "linen suit, dark sunglasses pushed up on his head and a thin gold chain"
        ),
        personality="smooth, stylish, always one step ahead",
        voice_profile={"speed": 1.0, "pitch": 2, "vol": 1},
        speech="Smooth and fast Hindi with a smile in the voice.",
    ),
    "falcon_advisor": Character(
        character_id="falcon_advisor",
        label="बाज़ साहब",
        role="The right-hand advisor who explains the plan",
        look=(
            "an original anthropomorphic falcon advisor with a human-like body, slate-grey "
            "feathers, a sharp yellow beak and clever, alert eyes. He wears a dark grey "
            "waistcoat over a white shirt with reading glasses on a chain"
        ),
        personality="precise, helpful, explains in one line",
        voice_profile={"speed": 1.08, "pitch": 3, "vol": 1},
        speech="Quick, precise Hindi. He explains the trick in a single line.",
    ),
    "desi_hawk_broker": Character(
        character_id="desi_hawk_broker",
        label="बाज़ दा दलाल",
        role="The street broker with the loudest deals",
        look=(
            "an original anthropomorphic hawk broker with a human-like body, brown feathers, "
            "a hooked beak and restless bright eyes. He wears an open kurta over a shirt, "
            "a gold chain and a watch on his wrist"
        ),
        personality="street-smart, fast-talking, always selling",
        voice_profile={"speed": 1.12, "pitch": 4, "vol": 1},
        speech="Fast street Hindi with rhythm and rhyme.",
    ),
}

DEFAULT_CHARACTER_ID = "white_lion_boss"


def get_character(character_id: str | None) -> Character:
    """Return a character, falling back to the default one."""
    if character_id and character_id in CHARACTERS:
        return CHARACTERS[character_id]
    return CHARACTERS[DEFAULT_CHARACTER_ID]


def default_character() -> Character:
    return CHARACTERS[DEFAULT_CHARACTER_ID]


def character_list() -> list[dict[str, object]]:
    return [
        {
            "character_id": character.character_id,
            "label": character.label,
            "role": character.role,
            "default": character.default,
            "voice_profile": character.voice_profile,
        }
        for character in CHARACTERS.values()
    ]


def cast_prompt(cast: list[Character]) -> str:
    """The cast description injected into the writer prompt."""
    lines = []
    for character in cast:
        lines.append(
            f"- {character.label} ({character.character_id}): {character.personality}. "
            f"{character.speech}"
        )
    return "\n".join(lines)
